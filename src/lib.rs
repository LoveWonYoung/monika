use std::collections::VecDeque;
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::slice;

const PCI_TYPE_SINGLE_FRAME: u8 = 0x00;
const PCI_TYPE_FIRST_FRAME: u8 = 0x10;
const PCI_TYPE_CONSECUTIVE_FRAME: u8 = 0x20;
const PCI_TYPE_FLOW_CONTROL: u8 = 0x30;
const MIN_FRAME_LEN: usize = 8;

const FLOW_STATUS_CTS: u8 = 0x00;
const FLOW_STATUS_WAIT: u8 = 0x01;
const FLOW_STATUS_OVERFLOW: u8 = 0x02;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CanFrame {
    pub id: u32,
    pub data: Vec<u8>,
    pub is_fd: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TpConfig {
    pub n_bs_ms: u32,
    pub n_cr_ms: u32,
    pub stmin_ms: u8,
    pub block_size: u8,
    pub tx_padding: TxPaddingMode,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TxPaddingMode {
    Raw,
    Min8,
    Dlc,
}

impl Default for TpConfig {
    fn default() -> Self {
        Self {
            n_bs_ms: 1000,
            n_cr_ms: 1000,
            stmin_ms: 20,
            block_size: 0,
            tx_padding: TxPaddingMode::Dlc,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TpError {
    InvalidConfig(&'static str),
    InvalidCanFrame(&'static str),
    InvalidPayload(&'static str),
    TxBusy,
    FunctionalMultiFrameNotSupported,
    TxTimeoutBs,
    RxTimeoutCr,
    SequenceMismatch { expected: u8, got: u8 },
    FlowControlOverflow,
    UnexpectedFlowStatus(u8),
    ParseError(&'static str),
}

impl Display for TpError {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            TpError::InvalidConfig(msg) => write!(f, "invalid config: {msg}"),
            TpError::InvalidCanFrame(msg) => write!(f, "invalid can frame: {msg}"),
            TpError::InvalidPayload(msg) => write!(f, "invalid payload: {msg}"),
            TpError::TxBusy => write!(f, "transport is busy transmitting"),
            TpError::FunctionalMultiFrameNotSupported => {
                write!(f, "functional multi-frame request is not supported")
            }
            TpError::TxTimeoutBs => write!(f, "timeout waiting flow control (N_Bs)"),
            TpError::RxTimeoutCr => write!(f, "timeout waiting consecutive frame (N_Cr)"),
            TpError::SequenceMismatch { expected, got } => {
                write!(f, "sequence mismatch: expected {expected}, got {got}")
            }
            TpError::FlowControlOverflow => write!(f, "peer reported flow control overflow"),
            TpError::UnexpectedFlowStatus(status) => {
                write!(f, "unexpected flow status: 0x{status:02X}")
            }
            TpError::ParseError(msg) => write!(f, "parse error: {msg}"),
        }
    }
}

impl Error for TpError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TxPhase {
    WaitFc,
    SendCf,
}

#[derive(Debug, Clone)]
struct TxState {
    target_id: u32,
    payload_tail: Vec<u8>,
    next_offset: usize,
    next_sn: u8,
    block_counter: u8,
    remote_block_size: u8,
    remote_stmin_us: u64,
    next_cf_at_us: u64,
    wait_fc_deadline_ms: u64,
    phase: TxPhase,
}

#[derive(Debug, Clone)]
struct RxState {
    total_len: usize,
    buffer: Vec<u8>,
    next_sn: u8,
    block_counter: u8,
    deadline_ms: u64,
}

pub struct IsoTpEngine {
    req_id: u32,
    resp_id: u32,
    func_id: u32,
    is_fd: bool,
    max_data_len: usize,
    cfg: TpConfig,
    tx_outgoing: VecDeque<CanFrame>,
    rx_completed: VecDeque<Vec<u8>>,
    errors: VecDeque<TpError>,
    tx_state: Option<TxState>,
    rx_state: Option<RxState>,
}

impl IsoTpEngine {
    pub fn init(
        req_id: u32,
        resp_id: u32,
        func_id: u32,
        is_fd: bool,
        cfg: TpConfig,
    ) -> Result<Self, TpError> {
        if cfg.n_bs_ms == 0 {
            return Err(TpError::InvalidConfig("n_bs_ms must be > 0"));
        }
        if cfg.n_cr_ms == 0 {
            return Err(TpError::InvalidConfig("n_cr_ms must be > 0"));
        }

        Ok(Self {
            req_id,
            resp_id,
            func_id,
            is_fd,
            max_data_len: if is_fd { 64 } else { 8 },
            cfg,
            tx_outgoing: VecDeque::new(),
            rx_completed: VecDeque::new(),
            errors: VecDeque::new(),
            tx_state: None,
            rx_state: None,
        })
    }

    pub fn on_can_frame(
        &mut self,
        id: u32,
        data: &[u8],
        is_fd: bool,
        ts_ms: u64,
    ) -> Result<(), TpError> {
        if data.is_empty() {
            return Err(TpError::InvalidCanFrame("empty payload"));
        }
        if is_fd != self.is_fd {
            return Ok(());
        }
        if id != self.resp_id {
            return Ok(());
        }

        let pci_type = data[0] & 0xF0;
        match pci_type {
            PCI_TYPE_FLOW_CONTROL => self.handle_flow_control(data, ts_ms),
            PCI_TYPE_SINGLE_FRAME => self.handle_rx_single_frame(data),
            PCI_TYPE_FIRST_FRAME => self.handle_rx_first_frame(data, ts_ms),
            PCI_TYPE_CONSECUTIVE_FRAME => self.handle_rx_consecutive_frame(data, ts_ms),
            _ => {
                let err = TpError::ParseError("unknown PCI type");
                self.push_error(err.clone());
                Err(err)
            }
        }
    }

    pub fn tx_uds_msg(
        &mut self,
        payload: &[u8],
        functional: bool,
        ts_ms: u64,
    ) -> Result<(), TpError> {
        if payload.is_empty() {
            return Err(TpError::InvalidPayload("payload must not be empty"));
        }
        if self.tx_state.is_some() {
            return Err(TpError::TxBusy);
        }

        let sf_limit = self.single_frame_limit();
        let target_id = if functional {
            self.func_id
        } else {
            self.req_id
        };

        if payload.len() <= sf_limit {
            let sf = build_single_frame_payload(payload, self.max_data_len)?;
            self.enqueue_can(target_id, sf);
            return Ok(());
        }

        if functional {
            return Err(TpError::FunctionalMultiFrameNotSupported);
        }

        let ff_pci_size = if payload.len() <= 4095 { 2 } else { 6 };
        if self.max_data_len <= ff_pci_size {
            return Err(TpError::InvalidConfig("max data length too small"));
        }
        let first_chunk_len = self.max_data_len - ff_pci_size;
        let first_chunk = &payload[..first_chunk_len];
        let ff = build_first_frame_payload(first_chunk, payload.len(), self.max_data_len)?;
        self.enqueue_can(target_id, ff);

        self.tx_state = Some(TxState {
            target_id,
            payload_tail: payload[first_chunk_len..].to_vec(),
            next_offset: 0,
            next_sn: 1,
            block_counter: 0,
            remote_block_size: 0,
            remote_stmin_us: 0,
            next_cf_at_us: ms_to_us(ts_ms),
            wait_fc_deadline_ms: ts_ms.saturating_add(self.cfg.n_bs_ms as u64),
            phase: TxPhase::WaitFc,
        });
        Ok(())
    }

    pub fn tick(&mut self, ts_ms: u64) -> Result<(), TpError> {
        if let Some(rx) = &self.rx_state {
            if ts_ms > rx.deadline_ms {
                self.rx_state = None;
                let err = TpError::RxTimeoutCr;
                self.push_error(err.clone());
                return Err(err);
            }
        }

        if let Some(tx) = &self.tx_state {
            if tx.phase == TxPhase::WaitFc && ts_ms > tx.wait_fc_deadline_ms {
                self.tx_state = None;
                let err = TpError::TxTimeoutBs;
                self.push_error(err.clone());
                return Err(err);
            }
        }

        self.send_due_consecutive_frames(ts_ms)?;
        Ok(())
    }

    pub fn pop_tx_can_frame(&mut self) -> Option<CanFrame> {
        self.tx_outgoing.pop_front()
    }

    pub fn rx_uds_msg(&mut self) -> Option<Vec<u8>> {
        self.rx_completed.pop_front()
    }

    pub fn pop_error(&mut self) -> Option<TpError> {
        self.errors.pop_front()
    }

    fn handle_flow_control(&mut self, data: &[u8], ts_ms: u64) -> Result<(), TpError> {
        if data.len() < 3 {
            let err = TpError::ParseError("flow control too short");
            self.push_error(err.clone());
            return Err(err);
        }

        let Some(tx) = self.tx_state.as_mut() else {
            return Ok(());
        };
        if tx.phase != TxPhase::WaitFc {
            return Ok(());
        }

        let flow_status = data[0] & 0x0F;
        match flow_status {
            FLOW_STATUS_CTS => {
                tx.remote_block_size = data[1];
                tx.remote_stmin_us = decode_stmin_to_us(data[2]);
                tx.block_counter = 0;
                tx.next_cf_at_us = ms_to_us(ts_ms).saturating_add(tx.remote_stmin_us);
                tx.phase = TxPhase::SendCf;
                self.send_due_consecutive_frames(ts_ms)?;
            }
            FLOW_STATUS_WAIT => {
                tx.wait_fc_deadline_ms = ts_ms.saturating_add(self.cfg.n_bs_ms as u64);
            }
            FLOW_STATUS_OVERFLOW => {
                self.tx_state = None;
                let err = TpError::FlowControlOverflow;
                self.push_error(err.clone());
                return Err(err);
            }
            other => {
                self.tx_state = None;
                let err = TpError::UnexpectedFlowStatus(other);
                self.push_error(err.clone());
                return Err(err);
            }
        }

        Ok(())
    }

    fn handle_rx_single_frame(&mut self, data: &[u8]) -> Result<(), TpError> {
        self.rx_state = None;

        let low_nibble_len = (data[0] & 0x0F) as usize;
        let (len, payload_start) = if low_nibble_len == 0 {
            if data.len() < 2 {
                let err = TpError::ParseError("single frame escaped length missing");
                self.push_error(err.clone());
                return Err(err);
            }
            (data[1] as usize, 2usize)
        } else {
            (low_nibble_len, 1usize)
        };

        if data.len() < payload_start + len {
            let err = TpError::ParseError("single frame payload too short");
            self.push_error(err.clone());
            return Err(err);
        }

        self.rx_completed
            .push_back(data[payload_start..payload_start + len].to_vec());
        Ok(())
    }

    fn handle_rx_first_frame(&mut self, data: &[u8], ts_ms: u64) -> Result<(), TpError> {
        self.rx_state = None;

        if data.len() < 2 {
            let err = TpError::ParseError("first frame too short");
            self.push_error(err.clone());
            return Err(err);
        }

        let mut total_len = (((data[0] & 0x0F) as usize) << 8) | data[1] as usize;
        let mut payload_start = 2usize;
        if total_len == 0 {
            if data.len() < 6 {
                let err = TpError::ParseError("long first frame too short");
                self.push_error(err.clone());
                return Err(err);
            }
            total_len = u32::from_be_bytes([data[2], data[3], data[4], data[5]]) as usize;
            payload_start = 6;
        }

        if total_len == 0 {
            let err = TpError::ParseError("first frame total length must be > 0");
            self.push_error(err.clone());
            return Err(err);
        }

        let mut buffer = Vec::with_capacity(total_len);
        if payload_start <= data.len() {
            let ff_payload = &data[payload_start..];
            let to_take = ff_payload.len().min(total_len);
            buffer.extend_from_slice(&ff_payload[..to_take]);
        }

        if buffer.len() >= total_len {
            buffer.truncate(total_len);
            self.rx_completed.push_back(buffer);
            return Ok(());
        }

        self.rx_state = Some(RxState {
            total_len,
            buffer,
            next_sn: 1,
            block_counter: 0,
            deadline_ms: ts_ms.saturating_add(self.cfg.n_cr_ms as u64),
        });

        let fc =
            build_flow_control_payload(FLOW_STATUS_CTS, self.cfg.block_size, self.cfg.stmin_ms);
        self.enqueue_can(self.req_id, fc);
        Ok(())
    }

    fn handle_rx_consecutive_frame(&mut self, data: &[u8], ts_ms: u64) -> Result<(), TpError> {
        if data.len() < 2 {
            let err = TpError::ParseError("consecutive frame too short");
            self.push_error(err.clone());
            return Err(err);
        }

        let Some(mut rx) = self.rx_state.take() else {
            return Ok(());
        };

        let sn = data[0] & 0x0F;
        if sn != rx.next_sn {
            let err = TpError::SequenceMismatch {
                expected: rx.next_sn,
                got: sn,
            };
            self.push_error(err.clone());
            return Err(err);
        }

        let remaining = rx.total_len.saturating_sub(rx.buffer.len());
        let to_take = remaining.min(data.len() - 1);
        rx.buffer.extend_from_slice(&data[1..1 + to_take]);
        rx.next_sn = (rx.next_sn + 1) & 0x0F;
        rx.deadline_ms = ts_ms.saturating_add(self.cfg.n_cr_ms as u64);

        if rx.buffer.len() >= rx.total_len {
            rx.buffer.truncate(rx.total_len);
            self.rx_completed.push_back(rx.buffer);
            return Ok(());
        }

        rx.block_counter = rx.block_counter.saturating_add(1);
        let mut should_send_fc = false;
        if self.cfg.block_size > 0 && rx.block_counter >= self.cfg.block_size {
            rx.block_counter = 0;
            should_send_fc = true;
        }

        self.rx_state = Some(rx);
        if should_send_fc {
            let fc =
                build_flow_control_payload(FLOW_STATUS_CTS, self.cfg.block_size, self.cfg.stmin_ms);
            self.enqueue_can(self.req_id, fc);
        }

        Ok(())
    }

    fn send_due_consecutive_frames(&mut self, ts_ms: u64) -> Result<(), TpError> {
        let ts_us = ms_to_us(ts_ms);
        loop {
            let Some(mut tx) = self.tx_state.take() else {
                return Ok(());
            };
            if tx.phase != TxPhase::SendCf {
                self.tx_state = Some(tx);
                return Ok(());
            }
            if ts_us < tx.next_cf_at_us {
                self.tx_state = Some(tx);
                return Ok(());
            }
            if tx.next_offset >= tx.payload_tail.len() {
                return Ok(());
            }

            let tail_remaining = tx.payload_tail.len() - tx.next_offset;
            let chunk_len = (self.max_data_len - 1).min(tail_remaining);
            let chunk_end = tx.next_offset + chunk_len;
            let chunk = &tx.payload_tail[tx.next_offset..chunk_end];
            let cf = build_consecutive_frame_payload(chunk, tx.next_sn)?;
            let target_id = tx.target_id;

            tx.next_sn = (tx.next_sn + 1) & 0x0F;
            tx.block_counter = tx.block_counter.saturating_add(1);
            tx.next_offset = chunk_end;
            self.enqueue_can(target_id, cf);

            if tx.next_offset >= tx.payload_tail.len() {
                return Ok(());
            }

            if tx.remote_block_size > 0 && tx.block_counter >= tx.remote_block_size {
                tx.block_counter = 0;
                tx.phase = TxPhase::WaitFc;
                tx.wait_fc_deadline_ms = ts_ms.saturating_add(self.cfg.n_bs_ms as u64);
                self.tx_state = Some(tx);
                return Ok(());
            }

            tx.next_cf_at_us = ts_us.saturating_add(tx.remote_stmin_us);
            let keep_sending_now = tx.remote_stmin_us == 0;
            self.tx_state = Some(tx);
            if !keep_sending_now {
                return Ok(());
            }
        }
    }

    fn enqueue_can(&mut self, id: u32, data: Vec<u8>) {
        let mut data = data;
        let target_len = match self.cfg.tx_padding {
            // Per requested behavior: raw also actively pads to at least 8 bytes.
            TxPaddingMode::Raw | TxPaddingMode::Min8 => MIN_FRAME_LEN,
            TxPaddingMode::Dlc => {
                if self.is_fd {
                    next_fd_target_length(data.len())
                } else {
                    MIN_FRAME_LEN
                }
            }
        };
        if data.len() < target_len {
            data.resize(target_len, 0);
        }
        self.tx_outgoing.push_back(CanFrame {
            id,
            data,
            is_fd: self.is_fd,
        });
    }

    fn single_frame_limit(&self) -> usize {
        if self.max_data_len <= 8 {
            7
        } else {
            self.max_data_len - 2
        }
    }

    fn push_error(&mut self, err: TpError) {
        self.errors.push_back(err);
    }
}

fn decode_stmin_to_us(stmin_byte: u8) -> u64 {
    if stmin_byte <= 0x7F {
        return (stmin_byte as u64).saturating_mul(1000);
    }
    if (0xF1..=0xF9).contains(&stmin_byte) {
        return ((stmin_byte - 0xF0) as u64).saturating_mul(100);
    }
    127_000
}

fn ms_to_us(ms: u64) -> u64 {
    ms.saturating_mul(1000)
}

fn next_fd_target_length(length: usize) -> usize {
    if length <= 8 {
        return 8;
    }
    if length <= 12 {
        return 12;
    }
    if length <= 16 {
        return 16;
    }
    if length <= 20 {
        return 20;
    }
    if length <= 24 {
        return 24;
    }
    if length <= 32 {
        return 32;
    }
    if length <= 48 {
        return 48;
    }
    64
}

fn build_flow_control_payload(status: u8, block_size: u8, stmin_ms: u8) -> Vec<u8> {
    let stmin_byte = if stmin_ms <= 127 { stmin_ms } else { 127 };
    vec![PCI_TYPE_FLOW_CONTROL | status, block_size, stmin_byte]
}

fn build_single_frame_payload(payload: &[u8], max_data_len: usize) -> Result<Vec<u8>, TpError> {
    let mut out = Vec::with_capacity(payload.len() + 2);
    if payload.len() <= 7 {
        out.push(PCI_TYPE_SINGLE_FRAME | (payload.len() as u8));
    } else {
        out.push(PCI_TYPE_SINGLE_FRAME);
        out.push(payload.len() as u8);
    }
    out.extend_from_slice(payload);
    if out.len() > max_data_len {
        return Err(TpError::InvalidPayload(
            "single-frame payload exceeds max data length",
        ));
    }
    Ok(out)
}

fn build_first_frame_payload(
    first_chunk: &[u8],
    total_len: usize,
    max_data_len: usize,
) -> Result<Vec<u8>, TpError> {
    let mut out = Vec::with_capacity(max_data_len);
    if total_len <= 4095 {
        out.push(PCI_TYPE_FIRST_FRAME | (((total_len >> 8) as u8) & 0x0F));
        out.push((total_len & 0xFF) as u8);
    } else {
        out.push(PCI_TYPE_FIRST_FRAME);
        out.push(0x00);
        out.extend_from_slice(&(total_len as u32).to_be_bytes());
    }
    out.extend_from_slice(first_chunk);
    if out.len() > max_data_len {
        return Err(TpError::InvalidPayload(
            "first-frame payload exceeds max data length",
        ));
    }
    Ok(out)
}

fn build_consecutive_frame_payload(chunk: &[u8], sn: u8) -> Result<Vec<u8>, TpError> {
    if sn > 0x0F {
        return Err(TpError::InvalidPayload("sequence number must be <= 0x0F"));
    }
    let mut out = Vec::with_capacity(chunk.len() + 1);
    out.push(PCI_TYPE_CONSECUTIVE_FRAME | (sn & 0x0F));
    out.extend_from_slice(chunk);
    Ok(out)
}

const ISOTP_FFI_OK: i32 = 0;
const ISOTP_FFI_HAS_ITEM: i32 = 1;
const ISOTP_FFI_ERR_NULL_PTR: i32 = -1;
const ISOTP_FFI_ERR_BUFFER_TOO_SMALL: i32 = -2;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct IsoTpConfigC {
    pub n_bs_ms: u32,
    pub n_cr_ms: u32,
    pub stmin_ms: u8,
    pub block_size: u8,
}

impl From<IsoTpConfigC> for TpConfig {
    fn from(value: IsoTpConfigC) -> Self {
        Self {
            n_bs_ms: value.n_bs_ms,
            n_cr_ms: value.n_cr_ms,
            stmin_ms: value.stmin_ms,
            block_size: value.block_size,
            tx_padding: TxPaddingMode::Dlc,
        }
    }
}

fn tp_error_to_code(err: &TpError) -> i32 {
    match err {
        TpError::InvalidConfig(_) => -100,
        TpError::InvalidCanFrame(_) => -101,
        TpError::InvalidPayload(_) => -102,
        TpError::TxBusy => -103,
        TpError::FunctionalMultiFrameNotSupported => -104,
        TpError::TxTimeoutBs => -105,
        TpError::RxTimeoutCr => -106,
        TpError::SequenceMismatch { .. } => -107,
        TpError::FlowControlOverflow => -108,
        TpError::UnexpectedFlowStatus(_) => -109,
        TpError::ParseError(_) => -110,
    }
}

fn map_result_code(res: Result<(), TpError>) -> i32 {
    match res {
        Ok(()) => ISOTP_FFI_OK,
        Err(err) => tp_error_to_code(&err),
    }
}

fn engine_mut<'a>(ptr: *mut IsoTpEngine) -> Result<&'a mut IsoTpEngine, i32> {
    if ptr.is_null() {
        return Err(ISOTP_FFI_ERR_NULL_PTR);
    }
    // SAFETY: Pointer nullability is checked above. Caller must pass a valid engine pointer
    // obtained from `isotp_engine_new` and keep exclusive mutable access.
    unsafe { Ok(&mut *ptr) }
}

fn data_slice<'a>(data_ptr: *const u8, data_len: usize) -> Result<&'a [u8], i32> {
    if data_len == 0 {
        return Ok(&[]);
    }
    if data_ptr.is_null() {
        return Err(ISOTP_FFI_ERR_NULL_PTR);
    }
    // SAFETY: Pointer nullability is checked above and caller provides valid readable memory.
    unsafe { Ok(slice::from_raw_parts(data_ptr, data_len)) }
}

fn out_data_slice<'a>(out_ptr: *mut u8, out_len: usize) -> Result<&'a mut [u8], i32> {
    if out_ptr.is_null() {
        return Err(ISOTP_FFI_ERR_NULL_PTR);
    }
    // SAFETY: Pointer nullability is checked above and caller provides valid writable memory.
    unsafe { Ok(slice::from_raw_parts_mut(out_ptr, out_len)) }
}

#[unsafe(no_mangle)]
pub extern "C" fn isotp_default_config() -> IsoTpConfigC {
    let cfg = TpConfig::default();
    IsoTpConfigC {
        n_bs_ms: cfg.n_bs_ms,
        n_cr_ms: cfg.n_cr_ms,
        stmin_ms: cfg.stmin_ms,
        block_size: cfg.block_size,
    }
}

#[unsafe(no_mangle)]
/// # Safety
/// `out_engine` must be a valid non-null pointer to writable memory for one `*mut IsoTpEngine`.
pub unsafe extern "C" fn isotp_engine_new(
    req_id: u32,
    resp_id: u32,
    func_id: u32,
    is_fd: u8,
    cfg: IsoTpConfigC,
    out_engine: *mut *mut IsoTpEngine,
) -> i32 {
    if out_engine.is_null() {
        return ISOTP_FFI_ERR_NULL_PTR;
    }

    let engine = match IsoTpEngine::init(req_id, resp_id, func_id, is_fd != 0, cfg.into()) {
        Ok(engine) => engine,
        Err(err) => return tp_error_to_code(&err),
    };

    let boxed = Box::new(engine);
    // SAFETY: out_engine is checked non-null above and points to writable memory provided by caller.
    unsafe {
        *out_engine = Box::into_raw(boxed);
    }
    ISOTP_FFI_OK
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a pointer previously returned by `isotp_engine_new` and freed at most once.
pub unsafe extern "C" fn isotp_engine_free(engine: *mut IsoTpEngine) {
    if engine.is_null() {
        return;
    }
    // SAFETY: Pointer comes from `Box::into_raw` in `isotp_engine_new`, and must be freed once.
    unsafe {
        drop(Box::from_raw(engine));
    }
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `isotp_engine_new`.
/// `data_ptr` must point to `data_len` readable bytes when `data_len > 0`.
pub unsafe extern "C" fn isotp_on_can_frame(
    engine: *mut IsoTpEngine,
    id: u32,
    data_ptr: *const u8,
    data_len: usize,
    is_fd: u8,
    ts_ms: u64,
) -> i32 {
    let engine = match engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };
    let data = match data_slice(data_ptr, data_len) {
        Ok(data) => data,
        Err(code) => return code,
    };
    map_result_code(engine.on_can_frame(id, data, is_fd != 0, ts_ms))
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `isotp_engine_new`.
/// `payload_ptr` must point to `payload_len` readable bytes when `payload_len > 0`.
pub unsafe extern "C" fn isotp_tx_uds_msg(
    engine: *mut IsoTpEngine,
    payload_ptr: *const u8,
    payload_len: usize,
    functional: u8,
    ts_ms: u64,
) -> i32 {
    let engine = match engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };
    let payload = match data_slice(payload_ptr, payload_len) {
        Ok(payload) => payload,
        Err(code) => return code,
    };
    map_result_code(engine.tx_uds_msg(payload, functional != 0, ts_ms))
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `isotp_engine_new`.
pub unsafe extern "C" fn isotp_tick(engine: *mut IsoTpEngine, ts_ms: u64) -> i32 {
    let engine = match engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };
    map_result_code(engine.tick(ts_ms))
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `isotp_engine_new`.
/// Output pointers must be valid for writes per their associated sizes.
pub unsafe extern "C" fn isotp_pop_tx_can_frame(
    engine: *mut IsoTpEngine,
    out_id: *mut u32,
    out_is_fd: *mut u8,
    out_data_ptr: *mut u8,
    out_data_cap: usize,
    out_data_len: *mut usize,
) -> i32 {
    let engine = match engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };

    let Some(frame) = engine.pop_tx_can_frame() else {
        return ISOTP_FFI_OK;
    };

    if out_id.is_null() || out_is_fd.is_null() || out_data_len.is_null() {
        engine.tx_outgoing.push_front(frame);
        return ISOTP_FFI_ERR_NULL_PTR;
    }

    if frame.data.len() > out_data_cap {
        engine.tx_outgoing.push_front(frame);
        return ISOTP_FFI_ERR_BUFFER_TOO_SMALL;
    }

    let out_data = match out_data_slice(out_data_ptr, out_data_cap) {
        Ok(data) => data,
        Err(code) => {
            engine.tx_outgoing.push_front(frame);
            return code;
        }
    };
    out_data[..frame.data.len()].copy_from_slice(&frame.data);

    // SAFETY: output pointers are checked non-null above and valid per caller contract.
    unsafe {
        *out_id = frame.id;
        *out_is_fd = u8::from(frame.is_fd);
        *out_data_len = frame.data.len();
    }
    ISOTP_FFI_HAS_ITEM
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `isotp_engine_new`.
/// `out_data_ptr` must be valid for `out_data_cap` writable bytes.
/// `out_data_len` must be a valid writable pointer.
pub unsafe extern "C" fn isotp_rx_uds_msg(
    engine: *mut IsoTpEngine,
    out_data_ptr: *mut u8,
    out_data_cap: usize,
    out_data_len: *mut usize,
) -> i32 {
    let engine = match engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };

    let Some(msg) = engine.rx_uds_msg() else {
        return ISOTP_FFI_OK;
    };

    if out_data_len.is_null() {
        engine.rx_completed.push_front(msg);
        return ISOTP_FFI_ERR_NULL_PTR;
    }
    if msg.len() > out_data_cap {
        engine.rx_completed.push_front(msg);
        return ISOTP_FFI_ERR_BUFFER_TOO_SMALL;
    }

    let out_data = match out_data_slice(out_data_ptr, out_data_cap) {
        Ok(data) => data,
        Err(code) => {
            engine.rx_completed.push_front(msg);
            return code;
        }
    };
    out_data[..msg.len()].copy_from_slice(&msg);

    // SAFETY: out_data_len is checked non-null above.
    unsafe {
        *out_data_len = msg.len();
    }
    ISOTP_FFI_HAS_ITEM
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `isotp_engine_new`.
/// `out_error_code` must be a valid writable pointer.
pub unsafe extern "C" fn isotp_pop_error(engine: *mut IsoTpEngine, out_error_code: *mut i32) -> i32 {
    let engine = match engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };

    let Some(err) = engine.pop_error() else {
        return ISOTP_FFI_OK;
    };

    if out_error_code.is_null() {
        engine.errors.push_front(err);
        return ISOTP_FFI_ERR_NULL_PTR;
    }

    // SAFETY: out_error_code is checked non-null above.
    unsafe {
        *out_error_code = tp_error_to_code(&err);
    }
    ISOTP_FFI_HAS_ITEM
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_single_frame_request_and_single_frame_response() {
        let mut engine =
            IsoTpEngine::init(0x7E0, 0x7E8, 0x7DF, false, TpConfig::default()).unwrap();

        engine.tx_uds_msg(&[0x22, 0xF1, 0x90], false, 0).unwrap();
        let tx = engine.pop_tx_can_frame().unwrap();
        assert_eq!(tx.id, 0x7E0);
        assert_eq!(tx.data, vec![0x03, 0x22, 0xF1, 0x90, 0x00, 0x00, 0x00, 0x00]);

        engine
            .on_can_frame(0x7E8, &[0x03, 0x62, 0xF1, 0x90], false, 1)
            .unwrap();
        let rx = engine.rx_uds_msg().unwrap();
        assert_eq!(rx, vec![0x62, 0xF1, 0x90]);
    }

    #[test]
    fn test_multi_frame_tx_with_flow_control() {
        let mut engine =
            IsoTpEngine::init(0x700, 0x708, 0x7DF, false, TpConfig::default()).unwrap();
        let payload = vec![0x36; 20];

        engine.tx_uds_msg(&payload, false, 0).unwrap();
        let ff = engine.pop_tx_can_frame().unwrap();
        assert_eq!(ff.id, 0x700);
        assert_eq!(ff.data[0] & 0xF0, PCI_TYPE_FIRST_FRAME);

        engine
            .on_can_frame(0x708, &[0x30, 0x00, 0x00], false, 1)
            .unwrap();

        let mut cf_count = 0;
        while let Some(frame) = engine.pop_tx_can_frame() {
            assert_eq!(frame.id, 0x700);
            assert_eq!(frame.data[0] & 0xF0, PCI_TYPE_CONSECUTIVE_FRAME);
            cf_count += 1;
        }
        assert!(cf_count > 0);
    }

    #[test]
    fn test_multi_frame_rx_sends_flow_control() {
        let mut engine =
            IsoTpEngine::init(0x7E0, 0x7E8, 0x7DF, false, TpConfig::default()).unwrap();

        engine
            .on_can_frame(
                0x7E8,
                &[0x10, 0x0A, 0x62, 0xF1, 0x90, 0x01, 0x02, 0x03],
                false,
                10,
            )
            .unwrap();
        let fc = engine.pop_tx_can_frame().unwrap();
        assert_eq!(fc.id, 0x7E0);
        assert_eq!(fc.data[0] & 0xF0, PCI_TYPE_FLOW_CONTROL);

        engine
            .on_can_frame(0x7E8, &[0x21, 0x04, 0x05, 0x06, 0x07], false, 11)
            .unwrap();
        let msg = engine.rx_uds_msg().unwrap();
        assert_eq!(
            msg,
            vec![0x62, 0xF1, 0x90, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]
        );
    }

    #[test]
    fn test_tick_timeout_for_wait_fc() {
        let mut engine = IsoTpEngine::init(
            0x700,
            0x708,
            0x7DF,
            false,
            TpConfig {
                n_bs_ms: 10,
                n_cr_ms: 1000,
                stmin_ms: 20,
                block_size: 0,
                tx_padding: TxPaddingMode::Dlc,
            },
        )
        .unwrap();

        engine.tx_uds_msg(&vec![0x55; 30], false, 0).unwrap();
        assert!(engine.tick(11).is_err());
        assert_eq!(engine.pop_error(), Some(TpError::TxTimeoutBs));
    }

    #[test]
    fn test_ffi_create_tx_and_free() {
        let mut engine_ptr: *mut IsoTpEngine = std::ptr::null_mut();
        let cfg = isotp_default_config();
        let rc = unsafe { isotp_engine_new(0x7E0, 0x7E8, 0x7DF, 0, cfg, &mut engine_ptr) };
        assert_eq!(rc, ISOTP_FFI_OK);
        assert!(!engine_ptr.is_null());

        let payload = [0x22u8, 0xF1, 0x90];
        let rc = unsafe { isotp_tx_uds_msg(engine_ptr, payload.as_ptr(), payload.len(), 0, 0) };
        assert_eq!(rc, ISOTP_FFI_OK);

        let mut out_id = 0u32;
        let mut out_is_fd = 0u8;
        let mut out_len = 0usize;
        let mut out_buf = [0u8; 64];
        let rc = unsafe {
            isotp_pop_tx_can_frame(
                engine_ptr,
                &mut out_id,
                &mut out_is_fd,
                out_buf.as_mut_ptr(),
                out_buf.len(),
                &mut out_len,
            )
        };
        assert_eq!(rc, ISOTP_FFI_HAS_ITEM);
        assert_eq!(out_id, 0x7E0);
        assert_eq!(out_is_fd, 0);
        assert_eq!(&out_buf[..out_len], &[0x03, 0x22, 0xF1, 0x90, 0x00, 0x00, 0x00, 0x00]);

        unsafe { isotp_engine_free(engine_ptr) };
    }

    #[test]
    fn test_canfd_single_frame_dlc_rounding_to_12() {
        let mut engine =
            IsoTpEngine::init(0x7E0, 0x7E8, 0x7DF, true, TpConfig::default()).unwrap();

        let payload = vec![0xA5; 9];
        engine.tx_uds_msg(&payload, false, 0).unwrap();
        let tx = engine.pop_tx_can_frame().unwrap();

        assert_eq!(tx.id, 0x7E0);
        assert!(tx.is_fd);
        assert_eq!(tx.data.len(), 12);
        assert_eq!(tx.data[0], 0x00);
        assert_eq!(tx.data[1], 9);
    }

    #[test]
    fn test_canfd_consecutive_frame_dlc_rounding_to_16() {
        let mut engine =
            IsoTpEngine::init(0x700, 0x708, 0x7DF, true, TpConfig::default()).unwrap();
        let payload = vec![0x36; 76];

        engine.tx_uds_msg(&payload, false, 0).unwrap();
        let ff = engine.pop_tx_can_frame().unwrap();
        assert_eq!(ff.data.len(), 64);
        assert_eq!(ff.data[0] & 0xF0, PCI_TYPE_FIRST_FRAME);

        engine
            .on_can_frame(0x708, &[0x30, 0x00, 0x00], true, 1)
            .unwrap();
        let cf = engine.pop_tx_can_frame().unwrap();
        assert_eq!(cf.data[0] & 0xF0, PCI_TYPE_CONSECUTIVE_FRAME);
        assert_eq!(cf.data.len(), 16);
    }

    #[test]
    fn test_canfd_min8_mode_keeps_non_dlc_length_above_8() {
        let mut engine = IsoTpEngine::init(
            0x7E0,
            0x7E8,
            0x7DF,
            true,
            TpConfig {
                tx_padding: TxPaddingMode::Min8,
                ..TpConfig::default()
            },
        )
        .unwrap();

        let payload = vec![0xA5; 9]; // SF escaped: 2 + 9 = 11 bytes
        engine.tx_uds_msg(&payload, false, 0).unwrap();
        let tx = engine.pop_tx_can_frame().unwrap();
        assert_eq!(tx.data.len(), 11);
    }

    #[test]
    fn test_canfd_raw_mode_pads_to_at_least_8() {
        let mut engine = IsoTpEngine::init(
            0x7E0,
            0x7E8,
            0x7DF,
            true,
            TpConfig {
                tx_padding: TxPaddingMode::Raw,
                ..TpConfig::default()
            },
        )
        .unwrap();

        let payload = vec![0x22, 0xF1, 0x90];
        engine.tx_uds_msg(&payload, false, 0).unwrap();
        let tx = engine.pop_tx_can_frame().unwrap();
        assert_eq!(tx.data.len(), 8);
    }

    #[test]
    fn test_decode_stmin_ms_range_to_us() {
        assert_eq!(decode_stmin_to_us(0x00), 0);
        assert_eq!(decode_stmin_to_us(0x14), 20_000);
        assert_eq!(decode_stmin_to_us(0x7F), 127_000);
    }

    #[test]
    fn test_decode_stmin_sub_ms_range_to_us() {
        assert_eq!(decode_stmin_to_us(0xF1), 100);
        assert_eq!(decode_stmin_to_us(0xF5), 500);
        assert_eq!(decode_stmin_to_us(0xF9), 900);
        assert_eq!(decode_stmin_to_us(0x80), 127_000);
    }
}
