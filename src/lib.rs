mod ffi;
mod transport;

pub use ffi::{
    IsoTpCanFrameInC, IsoTpConfigC, isotp_default_config, isotp_engine_free, isotp_engine_new,
    isotp_on_can_frame, isotp_on_can_frames, isotp_pop_error, isotp_pop_tx_can_frame,
    isotp_pop_tx_can_frames, isotp_rx_uds_msg, isotp_tick, isotp_tx_uds_msg,
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
