use std::collections::VecDeque;

use crate::transport::TpError;

pub const LIN_PCI_TYPE_SINGLE_FRAME: u8 = 0x00;
pub const LIN_PCI_TYPE_FIRST_FRAME: u8 = 0x10;
pub const LIN_PCI_TYPE_CONSECUTIVE_FRAME: u8 = 0x20;

pub const LIN_MASTER_DIAGNOSTIC_FRAME_ID: u8 = 0x3C;
pub const LIN_SLAVE_DIAGNOSTIC_FRAME_ID: u8 = 0x3D;
pub const LIN_BROADCAST_NAD: u8 = 0x7F;

const LIN_FRAME_LEN: usize = 8;
const LIN_SF_MAX_PAYLOAD_LEN: usize = 6;
const LIN_FF_FIRST_CHUNK_LEN: usize = 5;
const LIN_CF_CHUNK_LEN: usize = 6;
const LIN_MAX_TOTAL_LEN: usize = 4095;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LinFrame {
    pub id: u8,
    pub data: Vec<u8>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LinTpConfig {
    pub n_cr_ms: u32,
    pub max_pdu_len: usize,
}

impl Default for LinTpConfig {
    fn default() -> Self {
        Self {
            n_cr_ms: 1000,
            max_pdu_len: LIN_MAX_TOTAL_LEN,
        }
    }
}

#[derive(Debug, Clone)]
struct LinRxState {
    source_nad: u8,
    total_len: usize,
    buffer: Vec<u8>,
    next_sn: u8,
    deadline_ms: u64,
}

pub struct LinTpEngine {
    req_frame_id: u8,
    resp_frame_id: u8,
    req_nad: u8,
    func_nad: u8,
    cfg: LinTpConfig,
    tx_outgoing: VecDeque<LinFrame>,
    rx_completed: VecDeque<Vec<u8>>,
    errors: VecDeque<TpError>,
    rx_state: Option<LinRxState>,
}

impl LinTpEngine {
    pub fn init(
        req_frame_id: u8,
        resp_frame_id: u8,
        req_nad: u8,
        func_nad: u8,
        cfg: LinTpConfig,
    ) -> Result<Self, TpError> {
        if cfg.n_cr_ms == 0 {
            return Err(TpError::InvalidConfig("n_cr_ms must be > 0"));
        }
        if cfg.max_pdu_len == 0 {
            return Err(TpError::InvalidConfig("max_pdu_len must be > 0"));
        }
        if cfg.max_pdu_len > LIN_MAX_TOTAL_LEN {
            return Err(TpError::InvalidConfig("max_pdu_len must be <= 4095"));
        }

        Ok(Self {
            req_frame_id,
            resp_frame_id,
            req_nad,
            func_nad,
            cfg,
            tx_outgoing: VecDeque::new(),
            rx_completed: VecDeque::new(),
            errors: VecDeque::new(),
            rx_state: None,
        })
    }

    pub fn tx_uds_msg(
        &mut self,
        payload: &[u8],
        functional: bool,
        _ts_ms: u64,
    ) -> Result<(), TpError> {
        if payload.is_empty() {
            return Err(TpError::InvalidPayload("payload must not be empty"));
        }
        if payload.len() > self.cfg.max_pdu_len {
            return Err(TpError::InvalidPayload("payload exceeds max_pdu_len"));
        }
        if payload.len() > LIN_MAX_TOTAL_LEN {
            return Err(TpError::InvalidPayload(
                "payload exceeds LIN TP 12-bit length",
            ));
        }

        let nad = if functional {
            self.func_nad
        } else {
            self.req_nad
        };

        if payload.len() <= LIN_SF_MAX_PAYLOAD_LEN {
            let mut frame = [0u8; LIN_FRAME_LEN];
            frame[0] = nad;
            frame[1] = LIN_PCI_TYPE_SINGLE_FRAME | (payload.len() as u8);
            frame[2..2 + payload.len()].copy_from_slice(payload);
            self.enqueue_lin(self.req_frame_id, frame);
            return Ok(());
        }

        let mut ff = [0u8; LIN_FRAME_LEN];
        ff[0] = nad;
        ff[1] = LIN_PCI_TYPE_FIRST_FRAME | (((payload.len() >> 8) as u8) & 0x0F);
        ff[2] = (payload.len() & 0xFF) as u8;
        ff[3..3 + LIN_FF_FIRST_CHUNK_LEN].copy_from_slice(&payload[..LIN_FF_FIRST_CHUNK_LEN]);
        self.enqueue_lin(self.req_frame_id, ff);

        let mut offset = LIN_FF_FIRST_CHUNK_LEN;
        let mut sn = 1u8;
        while offset < payload.len() {
            let mut cf = [0u8; LIN_FRAME_LEN];
            cf[0] = nad;
            cf[1] = LIN_PCI_TYPE_CONSECUTIVE_FRAME | (sn & 0x0F);
            let chunk_len = LIN_CF_CHUNK_LEN.min(payload.len() - offset);
            cf[2..2 + chunk_len].copy_from_slice(&payload[offset..offset + chunk_len]);
            self.enqueue_lin(self.req_frame_id, cf);
            offset += chunk_len;
            sn = (sn + 1) & 0x0F;
        }

        Ok(())
    }

    pub fn set_nad(&mut self, req_nad: u8, func_nad: u8) {
        self.req_nad = req_nad;
        self.func_nad = func_nad;
    }

    pub fn on_lin_frame(&mut self, id: u8, data: &[u8], ts_ms: u64) -> Result<(), TpError> {
        if id != self.resp_frame_id {
            return Ok(());
        }
        if data.len() < 2 {
            return self.fail_parse("lin frame too short");
        }

        let pci = data[1];
        let pci_type = pci & 0xF0;
        match pci_type {
            LIN_PCI_TYPE_SINGLE_FRAME => self.handle_rx_single_frame(data),
            LIN_PCI_TYPE_FIRST_FRAME => self.handle_rx_first_frame(data, ts_ms),
            LIN_PCI_TYPE_CONSECUTIVE_FRAME => self.handle_rx_consecutive_frame(data, ts_ms),
            _ => self.fail_parse("unknown LIN PCI type"),
        }
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
        Ok(())
    }

    pub fn pop_tx_lin_frame(&mut self) -> Option<LinFrame> {
        self.tx_outgoing.pop_front()
    }

    pub fn rx_uds_msg(&mut self) -> Option<Vec<u8>> {
        self.rx_completed.pop_front()
    }

    pub fn pop_error(&mut self) -> Option<TpError> {
        self.errors.pop_front()
    }

    pub(crate) fn tx_front_data(&self) -> Option<(u8, &[u8])> {
        self.tx_outgoing
            .front()
            .map(|frame| (frame.id, frame.data.as_slice()))
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

    fn handle_rx_single_frame(&mut self, data: &[u8]) -> Result<(), TpError> {
        self.rx_state = None;
        let len = (data[1] & 0x0F) as usize;
        if len == 0 || len > LIN_SF_MAX_PAYLOAD_LEN {
            return self.fail_parse("invalid LIN single-frame length");
        }
        if data.len() < 2 + len {
            return self.fail_parse("single frame payload too short");
        }

        self.rx_completed.push_back(data[2..2 + len].to_vec());
        Ok(())
    }

    fn handle_rx_first_frame(&mut self, data: &[u8], ts_ms: u64) -> Result<(), TpError> {
        self.rx_state = None;
        if data.len() < 3 {
            return self.fail_parse("first frame too short");
        }

        let total_len = (((data[1] & 0x0F) as usize) << 8) | data[2] as usize;
        if total_len <= LIN_SF_MAX_PAYLOAD_LEN {
            return self.fail_parse("first frame total length must be > 6");
        }
        if total_len > self.cfg.max_pdu_len {
            return self.fail_parse("first frame total length exceeds max PDU length");
        }

        let mut buffer = Vec::with_capacity(total_len);
        let ff_payload = &data[3..];
        let to_take = ff_payload.len().min(total_len);
        buffer.extend_from_slice(&ff_payload[..to_take]);

        if buffer.len() >= total_len {
            buffer.truncate(total_len);
            self.rx_completed.push_back(buffer);
            return Ok(());
        }

        self.rx_state = Some(LinRxState {
            source_nad: data[0],
            total_len,
            buffer,
            next_sn: 1,
            deadline_ms: ts_ms.saturating_add(self.cfg.n_cr_ms as u64),
        });

        Ok(())
    }

    fn handle_rx_consecutive_frame(&mut self, data: &[u8], ts_ms: u64) -> Result<(), TpError> {
        if data.len() < 2 {
            return self.fail_parse("consecutive frame too short");
        }

        let Some(mut rx) = self.rx_state.take() else {
            return Ok(());
        };

        if data[0] != rx.source_nad {
            self.rx_state = Some(rx);
            return Ok(());
        }

        let sn = data[1] & 0x0F;
        if sn != rx.next_sn {
            let err = TpError::SequenceMismatch {
                expected: rx.next_sn,
                got: sn,
            };
            self.push_error(err.clone());
            return Err(err);
        }

        let remaining = rx.total_len.saturating_sub(rx.buffer.len());
        let to_take = remaining.min(data.len() - 2);
        rx.buffer.extend_from_slice(&data[2..2 + to_take]);
        rx.next_sn = (rx.next_sn + 1) & 0x0F;
        rx.deadline_ms = ts_ms.saturating_add(self.cfg.n_cr_ms as u64);

        if rx.buffer.len() >= rx.total_len {
            rx.buffer.truncate(rx.total_len);
            self.rx_completed.push_back(rx.buffer);
            return Ok(());
        }

        self.rx_state = Some(rx);
        Ok(())
    }

    fn enqueue_lin(&mut self, id: u8, payload: [u8; LIN_FRAME_LEN]) {
        self.tx_outgoing.push_back(LinFrame {
            id,
            data: payload.to_vec(),
        });
    }

    fn fail_parse(&mut self, msg: &'static str) -> Result<(), TpError> {
        let err = TpError::ParseError(msg);
        self.push_error(err.clone());
        Err(err)
    }

    fn push_error(&mut self, err: TpError) {
        self.errors.push_back(err);
    }
}
