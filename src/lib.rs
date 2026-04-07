mod ffi;
mod lin_transport;
mod transport;

pub use ffi::{
    IsoTpCanFrameInC, IsoTpConfigC, LinTpConfigC, isotp_default_config, isotp_engine_free,
    isotp_engine_new, isotp_on_can_frame, isotp_on_can_frames, isotp_pop_error,
    isotp_pop_tx_can_frame, isotp_pop_tx_can_frames, isotp_rx_uds_msg, isotp_tick,
    isotp_tx_uds_msg, lintp_default_config, lintp_engine_free, lintp_engine_new,
    lintp_on_lin_frame, lintp_pop_error, lintp_pop_tx_lin_frame, lintp_rx_uds_msg, lintp_tick,
    lintp_tx_uds_msg,
};
pub use lin_transport::{
    LIN_BROADCAST_NAD, LIN_MASTER_DIAGNOSTIC_FRAME_ID, LIN_PCI_TYPE_CONSECUTIVE_FRAME,
    LIN_PCI_TYPE_FIRST_FRAME, LIN_PCI_TYPE_SINGLE_FRAME, LIN_SLAVE_DIAGNOSTIC_FRAME_ID, LinFrame,
    LinTpConfig, LinTpEngine,
};
pub use transport::{CanFrame, IsoTpEngine, TpConfig, TpError, TxPaddingMode};

#[cfg(test)]
pub(crate) use ffi::{
    ISOTP_FFI_ERR_BUFFER_TOO_SMALL, ISOTP_FFI_ERR_NULL_PTR, ISOTP_FFI_HAS_ITEM, ISOTP_FFI_OK,
};
#[cfg(test)]
pub(crate) use transport::{
    PCI_TYPE_CONSECUTIVE_FRAME, PCI_TYPE_FIRST_FRAME, PCI_TYPE_FLOW_CONTROL, decode_stmin_to_us,
};

#[cfg(test)]
mod tests;
