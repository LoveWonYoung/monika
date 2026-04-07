use std::slice;

use crate::lin_transport::{LinTpConfig, LinTpEngine};
use crate::transport::{IsoTpEngine, TpConfig, TpError, TxPaddingMode};

pub(crate) const ISOTP_FFI_OK: i32 = 0;
pub(crate) const ISOTP_FFI_HAS_ITEM: i32 = 1;
pub(crate) const ISOTP_FFI_ERR_NULL_PTR: i32 = -1;
pub(crate) const ISOTP_FFI_ERR_BUFFER_TOO_SMALL: i32 = -2;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct IsoTpCanFrameInC {
    pub id: u32,
    pub is_fd: u8,
    pub data_ptr: *const u8,
    pub data_len: usize,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct IsoTpConfigC {
    pub n_bs_ms: u32,
    pub n_cr_ms: u32,
    pub stmin_ms: u8,
    pub block_size: u8,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct LinTpConfigC {
    pub n_cr_ms: u32,
    pub max_pdu_len: usize,
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

impl From<LinTpConfigC> for LinTpConfig {
    fn from(value: LinTpConfigC) -> Self {
        Self {
            n_cr_ms: value.n_cr_ms,
            max_pdu_len: value.max_pdu_len,
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

fn isotp_engine_mut<'a>(ptr: *mut IsoTpEngine) -> Result<&'a mut IsoTpEngine, i32> {
    if ptr.is_null() {
        return Err(ISOTP_FFI_ERR_NULL_PTR);
    }
    // SAFETY: Pointer nullability is checked above. Caller must pass a valid engine pointer
    // obtained from `isotp_engine_new` and keep exclusive mutable access.
    unsafe { Ok(&mut *ptr) }
}

fn lintp_engine_mut<'a>(ptr: *mut LinTpEngine) -> Result<&'a mut LinTpEngine, i32> {
    if ptr.is_null() {
        return Err(ISOTP_FFI_ERR_NULL_PTR);
    }
    // SAFETY: Pointer nullability is checked above. Caller must pass a valid engine pointer
    // obtained from `lintp_engine_new` and keep exclusive mutable access.
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

fn can_frames_slice<'a>(
    frames_ptr: *const IsoTpCanFrameInC,
    frame_count: usize,
) -> Result<&'a [IsoTpCanFrameInC], i32> {
    if frame_count == 0 {
        return Ok(&[]);
    }
    if frames_ptr.is_null() {
        return Err(ISOTP_FFI_ERR_NULL_PTR);
    }
    // SAFETY: pointer nullability is checked above and caller provides valid readable memory.
    unsafe { Ok(slice::from_raw_parts(frames_ptr, frame_count)) }
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
pub extern "C" fn lintp_default_config() -> LinTpConfigC {
    let cfg = LinTpConfig::default();
    LinTpConfigC {
        n_cr_ms: cfg.n_cr_ms,
        max_pdu_len: cfg.max_pdu_len,
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
    let engine = match isotp_engine_mut(engine) {
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
/// `frames_ptr` must point to `frame_count` readable `IsoTpCanFrameInC` entries when `frame_count > 0`.
/// Each non-empty frame entry must carry a readable `data_ptr` for its `data_len`.
/// `out_processed` may be null; when non-null it receives the number of frames successfully ingested.
pub unsafe extern "C" fn isotp_on_can_frames(
    engine: *mut IsoTpEngine,
    frames_ptr: *const IsoTpCanFrameInC,
    frame_count: usize,
    ts_ms: u64,
    out_processed: *mut usize,
) -> i32 {
    let engine = match isotp_engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };
    let frames = match can_frames_slice(frames_ptr, frame_count) {
        Ok(frames) => frames,
        Err(code) => return code,
    };

    let mut processed = 0usize;
    for frame in frames {
        let data = match data_slice(frame.data_ptr, frame.data_len) {
            Ok(data) => data,
            Err(code) => {
                if !out_processed.is_null() {
                    // SAFETY: out_processed is checked non-null above.
                    unsafe {
                        *out_processed = processed;
                    }
                }
                return code;
            }
        };
        let rc = map_result_code(engine.on_can_frame(frame.id, data, frame.is_fd != 0, ts_ms));
        if rc != ISOTP_FFI_OK {
            if !out_processed.is_null() {
                // SAFETY: out_processed is checked non-null above.
                unsafe {
                    *out_processed = processed;
                }
            }
            return rc;
        }
        processed += 1;
    }

    if !out_processed.is_null() {
        // SAFETY: out_processed is checked non-null above.
        unsafe {
            *out_processed = processed;
        }
    }
    ISOTP_FFI_OK
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
    let engine = match isotp_engine_mut(engine) {
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
    let engine = match isotp_engine_mut(engine) {
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
    let engine = match isotp_engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };

    let Some((id, is_fd, data)) = engine.tx_front_data() else {
        return ISOTP_FFI_OK;
    };

    if out_id.is_null() || out_is_fd.is_null() || out_data_len.is_null() {
        return ISOTP_FFI_ERR_NULL_PTR;
    }

    if data.len() > out_data_cap {
        return ISOTP_FFI_ERR_BUFFER_TOO_SMALL;
    }

    let out_data = match out_data_slice(out_data_ptr, out_data_cap) {
        Ok(data) => data,
        Err(code) => return code,
    };
    out_data[..data.len()].copy_from_slice(data);

    // SAFETY: output pointers are checked non-null above and valid per caller contract.
    unsafe {
        *out_id = id;
        *out_is_fd = u8::from(is_fd);
        *out_data_len = data.len();
    }
    engine.tx_drop_front_frame();
    ISOTP_FFI_HAS_ITEM
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `isotp_engine_new`.
/// When `max_frames > 0`, output pointers must be valid writable buffers sized for `max_frames`.
/// `out_data_ptr` must provide `max_frames * out_data_stride` writable bytes.
/// `out_count` must be a valid writable pointer.
pub unsafe extern "C" fn isotp_pop_tx_can_frames(
    engine: *mut IsoTpEngine,
    out_ids: *mut u32,
    out_is_fd: *mut u8,
    out_data_ptr: *mut u8,
    out_data_stride: usize,
    out_data_lens: *mut usize,
    max_frames: usize,
    out_count: *mut usize,
) -> i32 {
    let engine = match isotp_engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };
    if out_count.is_null() {
        return ISOTP_FFI_ERR_NULL_PTR;
    }
    // SAFETY: out_count is checked non-null above.
    unsafe {
        *out_count = 0;
    }
    if max_frames == 0 {
        return ISOTP_FFI_OK;
    }
    if out_ids.is_null() || out_is_fd.is_null() || out_data_ptr.is_null() || out_data_lens.is_null()
    {
        return ISOTP_FFI_ERR_NULL_PTR;
    }
    if out_data_stride == 0 {
        return ISOTP_FFI_ERR_BUFFER_TOO_SMALL;
    }
    let Some(out_data_total_len) = out_data_stride.checked_mul(max_frames) else {
        return ISOTP_FFI_ERR_BUFFER_TOO_SMALL;
    };

    // SAFETY: pointers are checked non-null above and caller guarantees buffer validity/sizes.
    let out_ids_slice = unsafe { slice::from_raw_parts_mut(out_ids, max_frames) };
    // SAFETY: pointers are checked non-null above and caller guarantees buffer validity/sizes.
    let out_is_fd_slice = unsafe { slice::from_raw_parts_mut(out_is_fd, max_frames) };
    // SAFETY: pointers are checked non-null above and caller guarantees buffer validity/sizes.
    let out_data_lens_slice = unsafe { slice::from_raw_parts_mut(out_data_lens, max_frames) };
    let out_data = match out_data_slice(out_data_ptr, out_data_total_len) {
        Ok(data) => data,
        Err(code) => return code,
    };

    let mut produced = 0usize;
    loop {
        if produced >= max_frames {
            break;
        }
        let Some((id, is_fd, data)) = engine.tx_front_data() else {
            break;
        };
        if data.len() > out_data_stride {
            if produced == 0 {
                return ISOTP_FFI_ERR_BUFFER_TOO_SMALL;
            }
            break;
        }
        let data_slot_start = produced * out_data_stride;
        out_data[data_slot_start..data_slot_start + data.len()].copy_from_slice(data);
        out_ids_slice[produced] = id;
        out_is_fd_slice[produced] = u8::from(is_fd);
        out_data_lens_slice[produced] = data.len();
        produced += 1;
        engine.tx_drop_front_frame();
    }

    // SAFETY: out_count is checked non-null above.
    unsafe {
        *out_count = produced;
    }
    if produced > 0 {
        ISOTP_FFI_HAS_ITEM
    } else {
        ISOTP_FFI_OK
    }
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
    let engine = match isotp_engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };

    let Some(msg) = engine.rx_front_msg() else {
        return ISOTP_FFI_OK;
    };

    if out_data_len.is_null() {
        return ISOTP_FFI_ERR_NULL_PTR;
    }
    if msg.len() > out_data_cap {
        return ISOTP_FFI_ERR_BUFFER_TOO_SMALL;
    }

    let out_data = match out_data_slice(out_data_ptr, out_data_cap) {
        Ok(data) => data,
        Err(code) => return code,
    };
    out_data[..msg.len()].copy_from_slice(msg);

    // SAFETY: out_data_len is checked non-null above.
    unsafe {
        *out_data_len = msg.len();
    }
    engine.rx_drop_front_msg();
    ISOTP_FFI_HAS_ITEM
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `isotp_engine_new`.
/// `out_error_code` must be a valid writable pointer.
pub unsafe extern "C" fn isotp_pop_error(
    engine: *mut IsoTpEngine,
    out_error_code: *mut i32,
) -> i32 {
    let engine = match isotp_engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };

    let Some(err) = engine.error_front() else {
        return ISOTP_FFI_OK;
    };

    if out_error_code.is_null() {
        return ISOTP_FFI_ERR_NULL_PTR;
    }

    // SAFETY: out_error_code is checked non-null above.
    unsafe {
        *out_error_code = tp_error_to_code(err);
    }
    engine.error_drop_front();
    ISOTP_FFI_HAS_ITEM
}

#[unsafe(no_mangle)]
/// # Safety
/// `out_engine` must be a valid non-null pointer to writable memory for one `*mut LinTpEngine`.
pub unsafe extern "C" fn lintp_engine_new(
    req_frame_id: u8,
    resp_frame_id: u8,
    req_nad: u8,
    func_nad: u8,
    cfg: LinTpConfigC,
    out_engine: *mut *mut LinTpEngine,
) -> i32 {
    if out_engine.is_null() {
        return ISOTP_FFI_ERR_NULL_PTR;
    }

    let engine = match LinTpEngine::init(req_frame_id, resp_frame_id, req_nad, func_nad, cfg.into())
    {
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
/// `engine` must be a pointer previously returned by `lintp_engine_new` and freed at most once.
pub unsafe extern "C" fn lintp_engine_free(engine: *mut LinTpEngine) {
    if engine.is_null() {
        return;
    }
    // SAFETY: Pointer comes from `Box::into_raw` in `lintp_engine_new`, and must be freed once.
    unsafe {
        drop(Box::from_raw(engine));
    }
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `lintp_engine_new`.
/// `data_ptr` must point to `data_len` readable bytes when `data_len > 0`.
pub unsafe extern "C" fn lintp_on_lin_frame(
    engine: *mut LinTpEngine,
    id: u8,
    data_ptr: *const u8,
    data_len: usize,
    ts_ms: u64,
) -> i32 {
    let engine = match lintp_engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };
    let data = match data_slice(data_ptr, data_len) {
        Ok(data) => data,
        Err(code) => return code,
    };
    map_result_code(engine.on_lin_frame(id, data, ts_ms))
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `lintp_engine_new`.
/// `payload_ptr` must point to `payload_len` readable bytes when `payload_len > 0`.
pub unsafe extern "C" fn lintp_tx_uds_msg(
    engine: *mut LinTpEngine,
    payload_ptr: *const u8,
    payload_len: usize,
    functional: u8,
    ts_ms: u64,
) -> i32 {
    let engine = match lintp_engine_mut(engine) {
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
/// `engine` must be a valid pointer returned by `lintp_engine_new`.
pub unsafe extern "C" fn lintp_tick(engine: *mut LinTpEngine, ts_ms: u64) -> i32 {
    let engine = match lintp_engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };
    map_result_code(engine.tick(ts_ms))
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `lintp_engine_new`.
/// Output pointers must be valid for writes per their associated sizes.
pub unsafe extern "C" fn lintp_pop_tx_lin_frame(
    engine: *mut LinTpEngine,
    out_id: *mut u8,
    out_data_ptr: *mut u8,
    out_data_cap: usize,
    out_data_len: *mut usize,
) -> i32 {
    let engine = match lintp_engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };

    let Some((id, data)) = engine.tx_front_data() else {
        return ISOTP_FFI_OK;
    };

    if out_id.is_null() || out_data_len.is_null() {
        return ISOTP_FFI_ERR_NULL_PTR;
    }
    if data.len() > out_data_cap {
        return ISOTP_FFI_ERR_BUFFER_TOO_SMALL;
    }

    let out_data = match out_data_slice(out_data_ptr, out_data_cap) {
        Ok(data) => data,
        Err(code) => return code,
    };
    out_data[..data.len()].copy_from_slice(data);

    // SAFETY: output pointers are checked non-null above and valid per caller contract.
    unsafe {
        *out_id = id;
        *out_data_len = data.len();
    }
    engine.tx_drop_front_frame();
    ISOTP_FFI_HAS_ITEM
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `lintp_engine_new`.
/// `out_data_ptr` must be valid for `out_data_cap` writable bytes.
/// `out_data_len` must be a valid writable pointer.
pub unsafe extern "C" fn lintp_rx_uds_msg(
    engine: *mut LinTpEngine,
    out_data_ptr: *mut u8,
    out_data_cap: usize,
    out_data_len: *mut usize,
) -> i32 {
    let engine = match lintp_engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };

    let Some(msg) = engine.rx_front_msg() else {
        return ISOTP_FFI_OK;
    };

    if out_data_len.is_null() {
        return ISOTP_FFI_ERR_NULL_PTR;
    }
    if msg.len() > out_data_cap {
        return ISOTP_FFI_ERR_BUFFER_TOO_SMALL;
    }

    let out_data = match out_data_slice(out_data_ptr, out_data_cap) {
        Ok(data) => data,
        Err(code) => return code,
    };
    out_data[..msg.len()].copy_from_slice(msg);

    // SAFETY: out_data_len is checked non-null above.
    unsafe {
        *out_data_len = msg.len();
    }
    engine.rx_drop_front_msg();
    ISOTP_FFI_HAS_ITEM
}

#[unsafe(no_mangle)]
/// # Safety
/// `engine` must be a valid pointer returned by `lintp_engine_new`.
/// `out_error_code` must be a valid writable pointer.
pub unsafe extern "C" fn lintp_pop_error(
    engine: *mut LinTpEngine,
    out_error_code: *mut i32,
) -> i32 {
    let engine = match lintp_engine_mut(engine) {
        Ok(engine) => engine,
        Err(code) => return code,
    };

    let Some(err) = engine.error_front() else {
        return ISOTP_FFI_OK;
    };

    if out_error_code.is_null() {
        return ISOTP_FFI_ERR_NULL_PTR;
    }

    // SAFETY: out_error_code is checked non-null above.
    unsafe {
        *out_error_code = tp_error_to_code(err);
    }
    engine.error_drop_front();
    ISOTP_FFI_HAS_ITEM
}
