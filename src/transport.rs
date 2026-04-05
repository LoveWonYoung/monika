use std::collections::VecDeque;
use std::error::Error;
use std::fmt::{Display, Formatter};

pub(crate) const PCI_TYPE_SINGLE_FRAME: u8 = 0x00;
pub(crate) const PCI_TYPE_FIRST_FRAME: u8 = 0x10;
pub(crate) const PCI_TYPE_CONSECUTIVE_FRAME: u8 = 0x20;
pub(crate) const PCI_TYPE_FLOW_CONTROL: u8 = 0x30;
const MIN_FRAME_LEN: usize = 8;
const MAX_PDU_LEN: usize = 8 * 1024;
const MAX_CAN_FRAME_DATA_LEN: usize = 64;

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
struct QueuedCanFrame {
    id: u32,
    data: [u8; MAX_CAN_FRAME_DATA_LEN],
    data_len: usize,
    is_fd: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct FramePayload {
    data: [u8; MAX_CAN_FRAME_DATA_LEN],
    len: usize,
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
    max_pdu_len: usize,
    cfg: TpConfig,
    tx_outgoing: VecDeque<QueuedCanFrame>,
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
            max_pdu_len: MAX_PDU_LEN,
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
        self.tx_outgoing.pop_front().map(|frame| CanFrame {
            id: frame.id,
            data: frame.data[..frame.data_len].to_vec(),
            is_fd: frame.is_fd,
        })
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
        if total_len > self.max_pdu_len {
            let err = TpError::ParseError("first frame total length exceeds max PDU length");
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

    fn enqueue_can(&mut self, id: u32, mut payload: FramePayload) {
        let target_len = match self.cfg.tx_padding {
            // Per requested behavior: raw also actively pads to at least 8 bytes.
            TxPaddingMode::Raw | TxPaddingMode::Min8 => MIN_FRAME_LEN,
            TxPaddingMode::Dlc => {
                if self.is_fd {
                    next_fd_target_length(payload.len)
                } else {
                    MIN_FRAME_LEN
                }
            }
        };
        if payload.len < target_len {
            payload.data[payload.len..target_len].fill(0);
            payload.len = target_len;
        }
        self.tx_outgoing.push_back(QueuedCanFrame {
            id,
            data: payload.data,
            data_len: payload.len,
            is_fd: self.is_fd,
        });
    }

    pub(crate) fn tx_front_data(&self) -> Option<(u32, bool, &[u8])> {
        self.tx_outgoing
            .front()
            .map(|frame| (frame.id, frame.is_fd, &frame.data[..frame.data_len]))
    }

    pub(crate) fn tx_drop_front_frame(&mut self) {
        let _ = self.tx_outgoing.pop_front();
    }

    pub(crate) fn rx_front_msg(&self) -> Option<&[u8]> {
        self.rx_completed.front().map(Vec::as_slice)
    }

    pub(crate) fn rx_drop_front_msg(&mut self) {
        let _ = self.rx_completed.pop_front();
    }

    pub(crate) fn error_front(&self) -> Option<&TpError> {
        self.errors.front()
    }

    pub(crate) fn error_drop_front(&mut self) {
        let _ = self.errors.pop_front();
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

pub(crate) fn decode_stmin_to_us(stmin_byte: u8) -> u64 {
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

fn build_flow_control_payload(status: u8, block_size: u8, stmin_ms: u8) -> FramePayload {
    let stmin_byte = if stmin_ms <= 127 { stmin_ms } else { 127 };
    let mut data = [0u8; MAX_CAN_FRAME_DATA_LEN];
    data[0] = PCI_TYPE_FLOW_CONTROL | status;
    data[1] = block_size;
    data[2] = stmin_byte;
    FramePayload { data, len: 3 }
}

fn build_single_frame_payload(
    payload: &[u8],
    max_data_len: usize,
) -> Result<FramePayload, TpError> {
    let mut data = [0u8; MAX_CAN_FRAME_DATA_LEN];
    let (header_len, out_len): (usize, usize) = if payload.len() <= 7 {
        (1, 1 + payload.len())
    } else {
        (2, 2 + payload.len())
    };
    if out_len > max_data_len {
        return Err(TpError::InvalidPayload(
            "single-frame payload exceeds max data length",
        ));
    }
    if payload.len() <= 7 {
        data[0] = PCI_TYPE_SINGLE_FRAME | (payload.len() as u8);
        data[1..1 + payload.len()].copy_from_slice(payload);
    } else {
        data[0] = PCI_TYPE_SINGLE_FRAME;
        data[1] = payload.len() as u8;
        data[2..2 + payload.len()].copy_from_slice(payload);
    }
    Ok(FramePayload {
        data,
        len: header_len + payload.len(),
    })
}

fn build_first_frame_payload(
    first_chunk: &[u8],
    total_len: usize,
    max_data_len: usize,
) -> Result<FramePayload, TpError> {
    let mut data = [0u8; MAX_CAN_FRAME_DATA_LEN];
    let mut header_len = 2usize;
    if total_len <= 4095 {
        data[0] = PCI_TYPE_FIRST_FRAME | (((total_len >> 8) as u8) & 0x0F);
        data[1] = (total_len & 0xFF) as u8;
    } else {
        header_len = 6;
        data[0] = PCI_TYPE_FIRST_FRAME;
        data[1] = 0x00;
        data[2..6].copy_from_slice(&(total_len as u32).to_be_bytes());
    }
    let out_len = header_len + first_chunk.len();
    if out_len > max_data_len {
        return Err(TpError::InvalidPayload(
            "first-frame payload exceeds max data length",
        ));
    }
    data[header_len..out_len].copy_from_slice(first_chunk);
    Ok(FramePayload { data, len: out_len })
}

fn build_consecutive_frame_payload(chunk: &[u8], sn: u8) -> Result<FramePayload, TpError> {
    if sn > 0x0F {
        return Err(TpError::InvalidPayload("sequence number must be <= 0x0F"));
    }
    let mut data = [0u8; MAX_CAN_FRAME_DATA_LEN];
    data[0] = PCI_TYPE_CONSECUTIVE_FRAME | (sn & 0x0F);
    data[1..1 + chunk.len()].copy_from_slice(chunk);
    Ok(FramePayload {
        data,
        len: 1 + chunk.len(),
    })
}
