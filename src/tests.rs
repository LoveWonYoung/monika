
use super::*;

#[test]
fn test_single_frame_request_and_single_frame_response() {
    let mut engine = IsoTpEngine::init(0x7E0, 0x7E8, 0x7DF, false, TpConfig::default()).unwrap();

    engine.tx_uds_msg(&[0x22, 0xF1, 0x90], false, 0).unwrap();
    let tx = engine.pop_tx_can_frame().unwrap();
    assert_eq!(tx.id, 0x7E0);
    assert_eq!(
        tx.data,
        vec![0x03, 0x22, 0xF1, 0x90, 0x00, 0x00, 0x00, 0x00]
    );

    engine
        .on_can_frame(0x7E8, &[0x03, 0x62, 0xF1, 0x90], false, 1)
        .unwrap();
    let rx = engine.rx_uds_msg().unwrap();
    assert_eq!(rx, vec![0x62, 0xF1, 0x90]);
}

#[test]
fn test_multi_frame_tx_with_flow_control() {
    let mut engine = IsoTpEngine::init(0x700, 0x708, 0x7DF, false, TpConfig::default()).unwrap();
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
    let mut engine = IsoTpEngine::init(0x7E0, 0x7E8, 0x7DF, false, TpConfig::default()).unwrap();

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
    assert_eq!(
        &out_buf[..out_len],
        &[0x03, 0x22, 0xF1, 0x90, 0x00, 0x00, 0x00, 0x00]
    );

    unsafe { isotp_engine_free(engine_ptr) };
}

#[test]
fn test_ffi_pop_tx_can_frame_null_ptr_keeps_queue_item() {
    let mut engine_ptr: *mut IsoTpEngine = std::ptr::null_mut();
    let cfg = isotp_default_config();
    let rc = unsafe { isotp_engine_new(0x7E0, 0x7E8, 0x7DF, 0, cfg, &mut engine_ptr) };
    assert_eq!(rc, ISOTP_FFI_OK);

    let payload = [0x22u8, 0xF1, 0x90];
    let rc = unsafe { isotp_tx_uds_msg(engine_ptr, payload.as_ptr(), payload.len(), 0, 0) };
    assert_eq!(rc, ISOTP_FFI_OK);

    let mut out_is_fd = 0u8;
    let mut out_len = 0usize;
    let mut out_buf = [0u8; 64];
    let rc = unsafe {
        isotp_pop_tx_can_frame(
            engine_ptr,
            std::ptr::null_mut(),
            &mut out_is_fd,
            out_buf.as_mut_ptr(),
            out_buf.len(),
            &mut out_len,
        )
    };
    assert_eq!(rc, ISOTP_FFI_ERR_NULL_PTR);

    let mut out_id = 0u32;
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
    assert_eq!(out_len, 8);

    unsafe { isotp_engine_free(engine_ptr) };
}

#[test]
fn test_ffi_rx_uds_msg_small_buffer_keeps_queue_item() {
    let mut engine_ptr: *mut IsoTpEngine = std::ptr::null_mut();
    let cfg = isotp_default_config();
    let rc = unsafe { isotp_engine_new(0x7E0, 0x7E8, 0x7DF, 0, cfg, &mut engine_ptr) };
    assert_eq!(rc, ISOTP_FFI_OK);

    let sf_resp = [0x03u8, 0x62, 0xF1, 0x90];
    let rc =
        unsafe { isotp_on_can_frame(engine_ptr, 0x7E8, sf_resp.as_ptr(), sf_resp.len(), 0, 1) };
    assert_eq!(rc, ISOTP_FFI_OK);

    let mut out_len = 0usize;
    let mut small_buf = [0u8; 2];
    let rc = unsafe {
        isotp_rx_uds_msg(
            engine_ptr,
            small_buf.as_mut_ptr(),
            small_buf.len(),
            &mut out_len,
        )
    };
    assert_eq!(rc, ISOTP_FFI_ERR_BUFFER_TOO_SMALL);

    let mut out_buf = [0u8; 64];
    let rc = unsafe {
        isotp_rx_uds_msg(
            engine_ptr,
            out_buf.as_mut_ptr(),
            out_buf.len(),
            &mut out_len,
        )
    };
    assert_eq!(rc, ISOTP_FFI_HAS_ITEM);
    assert_eq!(&out_buf[..out_len], &[0x62, 0xF1, 0x90]);

    unsafe { isotp_engine_free(engine_ptr) };
}

#[test]
fn test_ffi_pop_error_null_ptr_keeps_queue_item() {
    let mut engine_ptr: *mut IsoTpEngine = std::ptr::null_mut();
    let cfg = isotp_default_config();
    let rc = unsafe { isotp_engine_new(0x7E0, 0x7E8, 0x7DF, 0, cfg, &mut engine_ptr) };
    assert_eq!(rc, ISOTP_FFI_OK);

    let bad = [0x40u8];
    let rc = unsafe { isotp_on_can_frame(engine_ptr, 0x7E8, bad.as_ptr(), bad.len(), 0, 1) };
    assert_eq!(rc, -110);

    let rc = unsafe { isotp_pop_error(engine_ptr, std::ptr::null_mut()) };
    assert_eq!(rc, ISOTP_FFI_ERR_NULL_PTR);

    let mut out_err = 0i32;
    let rc = unsafe { isotp_pop_error(engine_ptr, &mut out_err) };
    assert_eq!(rc, ISOTP_FFI_HAS_ITEM);
    assert_eq!(out_err, -110);

    unsafe { isotp_engine_free(engine_ptr) };
}

#[test]
fn test_ffi_on_can_frames_batch_ingest() {
    let mut engine_ptr: *mut IsoTpEngine = std::ptr::null_mut();
    let cfg = isotp_default_config();
    let rc = unsafe { isotp_engine_new(0x7E0, 0x7E8, 0x7DF, 0, cfg, &mut engine_ptr) };
    assert_eq!(rc, ISOTP_FFI_OK);

    let sf1 = [0x03u8, 0x62, 0xF1, 0x90];
    let sf2 = [0x03u8, 0x62, 0xF1, 0x91];
    let frames = [
        IsoTpCanFrameInC {
            id: 0x7E8,
            is_fd: 0,
            data_ptr: sf1.as_ptr(),
            data_len: sf1.len(),
        },
        IsoTpCanFrameInC {
            id: 0x7E8,
            is_fd: 0,
            data_ptr: sf2.as_ptr(),
            data_len: sf2.len(),
        },
    ];

    let mut processed = 0usize;
    let rc = unsafe {
        isotp_on_can_frames(
            engine_ptr,
            frames.as_ptr(),
            frames.len(),
            10,
            &mut processed,
        )
    };
    assert_eq!(rc, ISOTP_FFI_OK);
    assert_eq!(processed, 2);

    let mut out = [0u8; 64];
    let mut out_len = 0usize;
    let rc = unsafe { isotp_rx_uds_msg(engine_ptr, out.as_mut_ptr(), out.len(), &mut out_len) };
    assert_eq!(rc, ISOTP_FFI_HAS_ITEM);
    assert_eq!(&out[..out_len], &[0x62, 0xF1, 0x90]);

    let rc = unsafe { isotp_rx_uds_msg(engine_ptr, out.as_mut_ptr(), out.len(), &mut out_len) };
    assert_eq!(rc, ISOTP_FFI_HAS_ITEM);
    assert_eq!(&out[..out_len], &[0x62, 0xF1, 0x91]);

    unsafe { isotp_engine_free(engine_ptr) };
}

#[test]
fn test_ffi_on_can_frames_batch_processed_on_error() {
    let mut engine_ptr: *mut IsoTpEngine = std::ptr::null_mut();
    let cfg = isotp_default_config();
    let rc = unsafe { isotp_engine_new(0x7E0, 0x7E8, 0x7DF, 0, cfg, &mut engine_ptr) };
    assert_eq!(rc, ISOTP_FFI_OK);

    let sf_ok = [0x03u8, 0x62, 0xF1, 0x90];
    let sf_bad = [0x40u8];
    let frames = [
        IsoTpCanFrameInC {
            id: 0x7E8,
            is_fd: 0,
            data_ptr: sf_ok.as_ptr(),
            data_len: sf_ok.len(),
        },
        IsoTpCanFrameInC {
            id: 0x7E8,
            is_fd: 0,
            data_ptr: sf_bad.as_ptr(),
            data_len: sf_bad.len(),
        },
    ];

    let mut processed = 0usize;
    let rc = unsafe {
        isotp_on_can_frames(
            engine_ptr,
            frames.as_ptr(),
            frames.len(),
            10,
            &mut processed,
        )
    };
    assert_eq!(rc, -110);
    assert_eq!(processed, 1);

    let mut out = [0u8; 64];
    let mut out_len = 0usize;
    let rc = unsafe { isotp_rx_uds_msg(engine_ptr, out.as_mut_ptr(), out.len(), &mut out_len) };
    assert_eq!(rc, ISOTP_FFI_HAS_ITEM);
    assert_eq!(&out[..out_len], &[0x62, 0xF1, 0x90]);

    unsafe { isotp_engine_free(engine_ptr) };
}

#[test]
fn test_ffi_pop_tx_can_frames_batch_and_stride_guard() {
    let mut engine_ptr: *mut IsoTpEngine = std::ptr::null_mut();
    let cfg = isotp_default_config();
    let rc = unsafe { isotp_engine_new(0x7E0, 0x7E8, 0x7DF, 0, cfg, &mut engine_ptr) };
    assert_eq!(rc, ISOTP_FFI_OK);

    let p1 = [0x22u8, 0xF1, 0x90];
    let p2 = [0x19u8, 0x02];
    let rc = unsafe { isotp_tx_uds_msg(engine_ptr, p1.as_ptr(), p1.len(), 0, 0) };
    assert_eq!(rc, ISOTP_FFI_OK);
    let rc = unsafe { isotp_tx_uds_msg(engine_ptr, p2.as_ptr(), p2.len(), 0, 0) };
    assert_eq!(rc, ISOTP_FFI_OK);

    let mut out_ids = [0u32; 2];
    let mut out_is_fd = [0u8; 2];
    let mut out_lens = [0usize; 2];
    let mut out_data = [0u8; 16];
    let mut out_count = 0usize;
    let rc = unsafe {
        isotp_pop_tx_can_frames(
            engine_ptr,
            out_ids.as_mut_ptr(),
            out_is_fd.as_mut_ptr(),
            out_data.as_mut_ptr(),
            8,
            out_lens.as_mut_ptr(),
            2,
            &mut out_count,
        )
    };
    assert_eq!(rc, ISOTP_FFI_HAS_ITEM);
    assert_eq!(out_count, 2);
    assert_eq!(out_ids[0], 0x7E0);
    assert_eq!(out_ids[1], 0x7E0);
    assert_eq!(out_is_fd[0], 0);
    assert_eq!(out_is_fd[1], 0);
    assert_eq!(&out_data[0..4], &[0x03, 0x22, 0xF1, 0x90]);
    assert_eq!(&out_data[8..11], &[0x02, 0x19, 0x02]);

    let rc = unsafe { isotp_tx_uds_msg(engine_ptr, p1.as_ptr(), p1.len(), 0, 0) };
    assert_eq!(rc, ISOTP_FFI_OK);

    let mut tiny_data = [0u8; 7];
    let rc = unsafe {
        isotp_pop_tx_can_frames(
            engine_ptr,
            out_ids.as_mut_ptr(),
            out_is_fd.as_mut_ptr(),
            tiny_data.as_mut_ptr(),
            7,
            out_lens.as_mut_ptr(),
            1,
            &mut out_count,
        )
    };
    assert_eq!(rc, ISOTP_FFI_ERR_BUFFER_TOO_SMALL);
    assert_eq!(out_count, 0);

    let mut out_one = [0u8; 8];
    let rc = unsafe {
        isotp_pop_tx_can_frames(
            engine_ptr,
            out_ids.as_mut_ptr(),
            out_is_fd.as_mut_ptr(),
            out_one.as_mut_ptr(),
            8,
            out_lens.as_mut_ptr(),
            1,
            &mut out_count,
        )
    };
    assert_eq!(rc, ISOTP_FFI_HAS_ITEM);
    assert_eq!(out_count, 1);
    assert_eq!(&out_one[..4], &[0x03, 0x22, 0xF1, 0x90]);

    unsafe { isotp_engine_free(engine_ptr) };
}

#[test]
fn test_canfd_single_frame_dlc_rounding_to_12() {
    let mut engine = IsoTpEngine::init(0x7E0, 0x7E8, 0x7DF, true, TpConfig::default()).unwrap();

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
    let mut engine = IsoTpEngine::init(0x700, 0x708, 0x7DF, true, TpConfig::default()).unwrap();
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

#[test]
fn test_rx_first_frame_over_max_pdu_len_rejected() {
    let mut engine = IsoTpEngine::init(0x7E0, 0x7E8, 0x7DF, false, TpConfig::default()).unwrap();

    // long FF length = 8193 (0x00002001), above MAX_PDU_LEN (8192)
    let rc = engine.on_can_frame(0x7E8, &[0x10, 0x00, 0x00, 0x00, 0x20, 0x01], false, 1);
    assert!(rc.is_err());
    assert_eq!(
        engine.pop_error(),
        Some(TpError::ParseError(
            "first frame total length exceeds max PDU length"
        ))
    );
    assert!(engine.rx_uds_msg().is_none());
}
