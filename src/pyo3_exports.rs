use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{LazyLock, Mutex};

use pyo3::prelude::*;
use pyo3::types::PyModule;

use crate::ffi::{
    ISOTP_FFI_ERR_BUFFER_TOO_SMALL, ISOTP_FFI_ERR_NULL_PTR, ISOTP_FFI_HAS_ITEM, ISOTP_FFI_OK,
};
use crate::lin_transport::{LinTpConfig, LinTpEngine};
use crate::transport::{IsoTpEngine, TpConfig, TpError, TxPaddingMode};

static NEXT_ISOTP_HANDLE: AtomicU64 = AtomicU64::new(1);
static NEXT_LINTP_HANDLE: AtomicU64 = AtomicU64::new(1);

static ISOTP_ENGINES: LazyLock<Mutex<HashMap<u64, IsoTpEngine>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));
static LINTP_ENGINES: LazyLock<Mutex<HashMap<u64, LinTpEngine>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

fn next_handle(counter: &AtomicU64) -> u64 {
    let mut handle = counter.fetch_add(1, Ordering::Relaxed);
    if handle == 0 {
        handle = counter.fetch_add(1, Ordering::Relaxed);
    }
    handle
}

fn with_isotp_engine_mut<R>(handle: u64, f: impl FnOnce(&mut IsoTpEngine) -> R) -> Result<R, i32> {
    if handle == 0 {
        return Err(ISOTP_FFI_ERR_NULL_PTR);
    }
    let mut engines = match ISOTP_ENGINES.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    let Some(engine) = engines.get_mut(&handle) else {
        return Err(ISOTP_FFI_ERR_NULL_PTR);
    };
    Ok(f(engine))
}

fn with_lintp_engine_mut<R>(handle: u64, f: impl FnOnce(&mut LinTpEngine) -> R) -> Result<R, i32> {
    if handle == 0 {
        return Err(ISOTP_FFI_ERR_NULL_PTR);
    }
    let mut engines = match LINTP_ENGINES.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    let Some(engine) = engines.get_mut(&handle) else {
        return Err(ISOTP_FFI_ERR_NULL_PTR);
    };
    Ok(f(engine))
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

fn tuple_to_isotp_cfg(cfg: (u32, u32, u8, u8)) -> TpConfig {
    TpConfig {
        n_bs_ms: cfg.0,
        n_cr_ms: cfg.1,
        stmin_ms: cfg.2,
        block_size: cfg.3,
        tx_padding: TxPaddingMode::Dlc,
    }
}

fn tuple_to_lintp_cfg(cfg: (u32, usize)) -> LinTpConfig {
    LinTpConfig {
        n_cr_ms: cfg.0,
        max_pdu_len: cfg.1,
    }
}

#[pyfunction(name = "isotp_default_config")]
fn py_isotp_default_config() -> (u32, u32, u8, u8) {
    let cfg = TpConfig::default();
    (cfg.n_bs_ms, cfg.n_cr_ms, cfg.stmin_ms, cfg.block_size)
}

#[pyfunction(name = "lintp_default_config")]
fn py_lintp_default_config() -> (u32, usize) {
    let cfg = LinTpConfig::default();
    (cfg.n_cr_ms, cfg.max_pdu_len)
}

#[pyfunction(name = "isotp_engine_new")]
#[pyo3(signature = (req_id, resp_id, func_id, is_fd=false, cfg=None))]
fn py_isotp_engine_new(
    req_id: u32,
    resp_id: u32,
    func_id: u32,
    is_fd: bool,
    cfg: Option<(u32, u32, u8, u8)>,
) -> (i32, u64) {
    let cfg = cfg.map(tuple_to_isotp_cfg).unwrap_or_default();
    let engine = match IsoTpEngine::init(req_id, resp_id, func_id, is_fd, cfg) {
        Ok(engine) => engine,
        Err(err) => return (tp_error_to_code(&err), 0),
    };

    let handle = next_handle(&NEXT_ISOTP_HANDLE);
    let mut engines = match ISOTP_ENGINES.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    engines.insert(handle, engine);
    (ISOTP_FFI_OK, handle)
}

#[pyfunction(name = "isotp_engine_free")]
fn py_isotp_engine_free(engine_handle: u64) {
    if engine_handle == 0 {
        return;
    }
    let mut engines = match ISOTP_ENGINES.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    let _ = engines.remove(&engine_handle);
}

#[pyfunction(name = "isotp_on_can_frame")]
fn py_isotp_on_can_frame(
    engine_handle: u64,
    id: u32,
    data: Vec<u8>,
    is_fd: bool,
    ts_ms: u64,
) -> i32 {
    match with_isotp_engine_mut(engine_handle, |engine| {
        map_result_code(engine.on_can_frame(id, &data, is_fd, ts_ms))
    }) {
        Ok(code) => code,
        Err(code) => code,
    }
}

#[pyfunction(name = "isotp_on_can_frames")]
fn py_isotp_on_can_frames(
    engine_handle: u64,
    frames: Vec<(u32, Vec<u8>, bool)>,
    ts_ms: u64,
) -> (i32, usize) {
    match with_isotp_engine_mut(engine_handle, |engine| {
        let mut processed = 0usize;
        for (id, data, is_fd) in frames {
            let rc = map_result_code(engine.on_can_frame(id, &data, is_fd, ts_ms));
            if rc != ISOTP_FFI_OK {
                return (rc, processed);
            }
            processed += 1;
        }
        (ISOTP_FFI_OK, processed)
    }) {
        Ok(result) => result,
        Err(code) => (code, 0),
    }
}

#[pyfunction(name = "isotp_tx_uds_msg")]
fn py_isotp_tx_uds_msg(engine_handle: u64, payload: Vec<u8>, functional: bool, ts_ms: u64) -> i32 {
    match with_isotp_engine_mut(engine_handle, |engine| {
        map_result_code(engine.tx_uds_msg(&payload, functional, ts_ms))
    }) {
        Ok(code) => code,
        Err(code) => code,
    }
}

#[pyfunction(name = "isotp_tick")]
fn py_isotp_tick(engine_handle: u64, ts_ms: u64) -> i32 {
    match with_isotp_engine_mut(engine_handle, |engine| map_result_code(engine.tick(ts_ms))) {
        Ok(code) => code,
        Err(code) => code,
    }
}

#[pyfunction(name = "isotp_pop_tx_can_frame")]
fn py_isotp_pop_tx_can_frame(engine_handle: u64) -> (i32, u32, bool, Vec<u8>) {
    match with_isotp_engine_mut(engine_handle, |engine| match engine.pop_tx_can_frame() {
        Some(frame) => (ISOTP_FFI_HAS_ITEM, frame.id, frame.is_fd, frame.data),
        None => (ISOTP_FFI_OK, 0, false, Vec::new()),
    }) {
        Ok(result) => result,
        Err(code) => (code, 0, false, Vec::new()),
    }
}

#[pyfunction(name = "isotp_pop_tx_can_frames")]
#[pyo3(signature = (engine_handle, max_frames=64))]
fn py_isotp_pop_tx_can_frames(
    engine_handle: u64,
    max_frames: usize,
) -> (i32, Vec<(u32, Vec<u8>, bool)>) {
    match with_isotp_engine_mut(engine_handle, |engine| {
        if max_frames == 0 {
            return (ISOTP_FFI_OK, Vec::new());
        }

        let mut out = Vec::new();
        while out.len() < max_frames {
            let Some(frame) = engine.pop_tx_can_frame() else {
                break;
            };
            out.push((frame.id, frame.data, frame.is_fd));
        }

        if out.is_empty() {
            (ISOTP_FFI_OK, out)
        } else {
            (ISOTP_FFI_HAS_ITEM, out)
        }
    }) {
        Ok(result) => result,
        Err(code) => (code, Vec::new()),
    }
}

#[pyfunction(name = "isotp_rx_uds_msg")]
fn py_isotp_rx_uds_msg(engine_handle: u64) -> (i32, Vec<u8>) {
    match with_isotp_engine_mut(engine_handle, |engine| match engine.rx_uds_msg() {
        Some(msg) => (ISOTP_FFI_HAS_ITEM, msg),
        None => (ISOTP_FFI_OK, Vec::new()),
    }) {
        Ok(result) => result,
        Err(code) => (code, Vec::new()),
    }
}

#[pyfunction(name = "isotp_pop_error")]
fn py_isotp_pop_error(engine_handle: u64) -> (i32, i32) {
    match with_isotp_engine_mut(engine_handle, |engine| match engine.pop_error() {
        Some(err) => (ISOTP_FFI_HAS_ITEM, tp_error_to_code(&err)),
        None => (ISOTP_FFI_OK, 0),
    }) {
        Ok(result) => result,
        Err(code) => (code, 0),
    }
}

#[pyfunction(name = "lintp_engine_new")]
#[pyo3(signature = (req_frame_id, resp_frame_id, req_nad, func_nad, cfg=None))]
fn py_lintp_engine_new(
    req_frame_id: u8,
    resp_frame_id: u8,
    req_nad: u8,
    func_nad: u8,
    cfg: Option<(u32, usize)>,
) -> (i32, u64) {
    let cfg = cfg.map(tuple_to_lintp_cfg).unwrap_or_default();
    let engine = match LinTpEngine::init(req_frame_id, resp_frame_id, req_nad, func_nad, cfg) {
        Ok(engine) => engine,
        Err(err) => return (tp_error_to_code(&err), 0),
    };

    let handle = next_handle(&NEXT_LINTP_HANDLE);
    let mut engines = match LINTP_ENGINES.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    engines.insert(handle, engine);
    (ISOTP_FFI_OK, handle)
}

#[pyfunction(name = "lintp_engine_free")]
fn py_lintp_engine_free(engine_handle: u64) {
    if engine_handle == 0 {
        return;
    }
    let mut engines = match LINTP_ENGINES.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    let _ = engines.remove(&engine_handle);
}

#[pyfunction(name = "lintp_on_lin_frame")]
fn py_lintp_on_lin_frame(engine_handle: u64, id: u8, data: Vec<u8>, ts_ms: u64) -> i32 {
    match with_lintp_engine_mut(engine_handle, |engine| {
        map_result_code(engine.on_lin_frame(id, &data, ts_ms))
    }) {
        Ok(code) => code,
        Err(code) => code,
    }
}

#[pyfunction(name = "lintp_set_nad")]
fn py_lintp_set_nad(engine_handle: u64, req_nad: u8, func_nad: u8) -> i32 {
    match with_lintp_engine_mut(engine_handle, |engine| {
        engine.set_nad(req_nad, func_nad);
        ISOTP_FFI_OK
    }) {
        Ok(code) => code,
        Err(code) => code,
    }
}

#[pyfunction(name = "lintp_tx_uds_msg")]
fn py_lintp_tx_uds_msg(engine_handle: u64, payload: Vec<u8>, functional: bool, ts_ms: u64) -> i32 {
    match with_lintp_engine_mut(engine_handle, |engine| {
        map_result_code(engine.tx_uds_msg(&payload, functional, ts_ms))
    }) {
        Ok(code) => code,
        Err(code) => code,
    }
}

#[pyfunction(name = "lintp_tick")]
fn py_lintp_tick(engine_handle: u64, ts_ms: u64) -> i32 {
    match with_lintp_engine_mut(engine_handle, |engine| map_result_code(engine.tick(ts_ms))) {
        Ok(code) => code,
        Err(code) => code,
    }
}

#[pyfunction(name = "lintp_pop_tx_lin_frame")]
fn py_lintp_pop_tx_lin_frame(engine_handle: u64) -> (i32, u8, Vec<u8>) {
    match with_lintp_engine_mut(engine_handle, |engine| match engine.pop_tx_lin_frame() {
        Some(frame) => (ISOTP_FFI_HAS_ITEM, frame.id, frame.data),
        None => (ISOTP_FFI_OK, 0, Vec::new()),
    }) {
        Ok(result) => result,
        Err(code) => (code, 0, Vec::new()),
    }
}

#[pyfunction(name = "lintp_rx_uds_msg")]
fn py_lintp_rx_uds_msg(engine_handle: u64) -> (i32, Vec<u8>) {
    match with_lintp_engine_mut(engine_handle, |engine| match engine.rx_uds_msg() {
        Some(msg) => (ISOTP_FFI_HAS_ITEM, msg),
        None => (ISOTP_FFI_OK, Vec::new()),
    }) {
        Ok(result) => result,
        Err(code) => (code, Vec::new()),
    }
}

#[pyfunction(name = "lintp_pop_error")]
fn py_lintp_pop_error(engine_handle: u64) -> (i32, i32) {
    match with_lintp_engine_mut(engine_handle, |engine| match engine.pop_error() {
        Some(err) => (ISOTP_FFI_HAS_ITEM, tp_error_to_code(&err)),
        None => (ISOTP_FFI_OK, 0),
    }) {
        Ok(result) => result,
        Err(code) => (code, 0),
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("ISOTP_FFI_OK", ISOTP_FFI_OK)?;
    m.add("ISOTP_FFI_HAS_ITEM", ISOTP_FFI_HAS_ITEM)?;
    m.add("ISOTP_FFI_ERR_NULL_PTR", ISOTP_FFI_ERR_NULL_PTR)?;
    m.add(
        "ISOTP_FFI_ERR_BUFFER_TOO_SMALL",
        ISOTP_FFI_ERR_BUFFER_TOO_SMALL,
    )?;

    m.add_function(wrap_pyfunction!(py_isotp_default_config, m)?)?;
    m.add_function(wrap_pyfunction!(py_lintp_default_config, m)?)?;

    m.add_function(wrap_pyfunction!(py_isotp_engine_new, m)?)?;
    m.add_function(wrap_pyfunction!(py_isotp_engine_free, m)?)?;
    m.add_function(wrap_pyfunction!(py_isotp_on_can_frame, m)?)?;
    m.add_function(wrap_pyfunction!(py_isotp_on_can_frames, m)?)?;
    m.add_function(wrap_pyfunction!(py_isotp_tx_uds_msg, m)?)?;
    m.add_function(wrap_pyfunction!(py_isotp_tick, m)?)?;
    m.add_function(wrap_pyfunction!(py_isotp_pop_tx_can_frame, m)?)?;
    m.add_function(wrap_pyfunction!(py_isotp_pop_tx_can_frames, m)?)?;
    m.add_function(wrap_pyfunction!(py_isotp_rx_uds_msg, m)?)?;
    m.add_function(wrap_pyfunction!(py_isotp_pop_error, m)?)?;

    m.add_function(wrap_pyfunction!(py_lintp_engine_new, m)?)?;
    m.add_function(wrap_pyfunction!(py_lintp_engine_free, m)?)?;
    m.add_function(wrap_pyfunction!(py_lintp_on_lin_frame, m)?)?;
    m.add_function(wrap_pyfunction!(py_lintp_set_nad, m)?)?;
    m.add_function(wrap_pyfunction!(py_lintp_tx_uds_msg, m)?)?;
    m.add_function(wrap_pyfunction!(py_lintp_tick, m)?)?;
    m.add_function(wrap_pyfunction!(py_lintp_pop_tx_lin_frame, m)?)?;
    m.add_function(wrap_pyfunction!(py_lintp_rx_uds_msg, m)?)?;
    m.add_function(wrap_pyfunction!(py_lintp_pop_error, m)?)?;

    Ok(())
}
