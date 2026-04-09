#ifndef ISOTP_ENGINE_H
#define ISOTP_ENGINE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Opaque engine handle. */
typedef struct IsoTpEngine IsoTpEngine;
typedef struct LinTpEngine LinTpEngine;

/* C-compatible TP config (matches Rust repr(C) layout). */
typedef struct IsoTpConfigC {
    uint32_t n_bs_ms;
    uint32_t n_cr_ms;
    uint8_t stmin_ms;
    uint8_t block_size;
} IsoTpConfigC;

/* C-compatible LIN TP config (matches Rust repr(C) layout). */
typedef struct LinTpConfigC {
    uint32_t n_cr_ms;
    size_t max_pdu_len;
} LinTpConfigC;

/* C-compatible input CAN frame for batch ingest. */
typedef struct IsoTpCanFrameInC {
    uint32_t id;
    uint8_t is_fd;
    const uint8_t* data_ptr;
    size_t data_len;
} IsoTpCanFrameInC;

/* Generic FFI return codes. */
enum {
    ISOTP_FFI_OK = 0,
    ISOTP_FFI_HAS_ITEM = 1,
    ISOTP_FFI_ERR_NULL_PTR = -1,
    ISOTP_FFI_ERR_BUFFER_TOO_SMALL = -2
};

/* Mapped TP error codes (from Rust TpError). */
enum {
    ISOTP_ERR_INVALID_CONFIG = -100,
    ISOTP_ERR_INVALID_CAN_FRAME = -101,
    ISOTP_ERR_INVALID_PAYLOAD = -102,
    ISOTP_ERR_TX_BUSY = -103,
    ISOTP_ERR_FUNCTIONAL_MULTI_FRAME_NOT_SUPPORTED = -104,
    ISOTP_ERR_TX_TIMEOUT_BS = -105,
    ISOTP_ERR_RX_TIMEOUT_CR = -106,
    ISOTP_ERR_SEQUENCE_MISMATCH = -107,
    ISOTP_ERR_FLOW_CONTROL_OVERFLOW = -108,
    ISOTP_ERR_UNEXPECTED_FLOW_STATUS = -109,
    ISOTP_ERR_PARSE_ERROR = -110
};

/* Returns default transport config. */
IsoTpConfigC isotp_default_config(void);
LinTpConfigC lintp_default_config(void);

/*
 * Creates a new engine instance.
 * On success, writes an owned pointer to *out_engine and returns ISOTP_FFI_OK.
 */
int32_t isotp_engine_new(
    uint32_t req_id,
    uint32_t resp_id,
    uint32_t func_id,
    uint8_t is_fd,
    IsoTpConfigC cfg,
    IsoTpEngine** out_engine
);

/* Frees an engine created by isotp_engine_new (safe to pass NULL). */
void isotp_engine_free(IsoTpEngine* engine);

/* Feeds one CAN/CAN-FD frame into the TP engine. */
int32_t isotp_on_can_frame(
    IsoTpEngine* engine,
    uint32_t id,
    const uint8_t* data_ptr,
    size_t data_len,
    uint8_t is_fd,
    uint64_t ts_ms
);

/*
 * Feeds a batch of CAN/CAN-FD frames into the TP engine.
 * Returns ISOTP_FFI_OK on full success. On error, returns <0 and (if non-null)
 * writes how many frames were successfully processed before failure.
 */
int32_t isotp_on_can_frames(
    IsoTpEngine* engine,
    const IsoTpCanFrameInC* frames_ptr,
    size_t frame_count,
    uint64_t ts_ms,
    size_t* out_processed
);

/* Enqueues one UDS payload for TP transmit. */
int32_t isotp_tx_uds_msg(
    IsoTpEngine* engine,
    const uint8_t* payload_ptr,
    size_t payload_len,
    uint8_t functional,
    uint64_t ts_ms
);

/* Advances internal timers/state machine. */
int32_t isotp_tick(IsoTpEngine* engine, uint64_t ts_ms);

/*
 * Pops one pending CAN frame ready to transmit.
 * Returns:
 *   ISOTP_FFI_HAS_ITEM when one frame is written to outputs
 *   ISOTP_FFI_OK when queue is empty
 *   <0 on error
 */
int32_t isotp_pop_tx_can_frame(
    IsoTpEngine* engine,
    uint32_t* out_id,
    uint8_t* out_is_fd,
    uint8_t* out_data_ptr,
    size_t out_data_cap,
    size_t* out_data_len
);

/*
 * Pops up to max_frames pending CAN frames in one call.
 * Data is written as fixed-size slots into out_data_ptr with stride out_data_stride.
 * Returns:
 *   ISOTP_FFI_HAS_ITEM when one or more frames are written
 *   ISOTP_FFI_OK when queue is empty
 *   <0 on error
 */
int32_t isotp_pop_tx_can_frames(
    IsoTpEngine* engine,
    uint32_t* out_ids,
    uint8_t* out_is_fd,
    uint8_t* out_data_ptr,
    size_t out_data_stride,
    size_t* out_data_lens,
    size_t max_frames,
    size_t* out_count
);

/*
 * Pops one completed TP payload.
 * Returns:
 *   ISOTP_FFI_HAS_ITEM when one payload is written to outputs
 *   ISOTP_FFI_OK when queue is empty
 *   <0 on error
 */
int32_t isotp_rx_uds_msg(
    IsoTpEngine* engine,
    uint8_t* out_data_ptr,
    size_t out_data_cap,
    size_t* out_data_len
);

/*
 * Pops one async TP error code.
 * Returns:
 *   ISOTP_FFI_HAS_ITEM when one error code is written to out_error_code
 *   ISOTP_FFI_OK when queue is empty
 *   <0 on error
 */
int32_t isotp_pop_error(IsoTpEngine* engine, int32_t* out_error_code);

/*
 * LIN TP APIs (single-threaded; TP-only).
 */
int32_t lintp_engine_new(
    uint8_t req_frame_id,
    uint8_t resp_frame_id,
    uint8_t req_nad,
    uint8_t func_nad,
    LinTpConfigC cfg,
    LinTpEngine** out_engine
);

void lintp_engine_free(LinTpEngine* engine);

int32_t lintp_on_lin_frame(
    LinTpEngine* engine,
    uint8_t id,
    const uint8_t* data_ptr,
    size_t data_len,
    uint64_t ts_ms
);

int32_t lintp_set_nad(
    LinTpEngine* engine,
    uint8_t req_nad,
    uint8_t func_nad
);

int32_t lintp_tx_uds_msg(
    LinTpEngine* engine,
    const uint8_t* payload_ptr,
    size_t payload_len,
    uint8_t functional,
    uint64_t ts_ms
);

int32_t lintp_tick(LinTpEngine* engine, uint64_t ts_ms);

int32_t lintp_pop_tx_lin_frame(
    LinTpEngine* engine,
    uint8_t* out_id,
    uint8_t* out_data_ptr,
    size_t out_data_cap,
    size_t* out_data_len
);

int32_t lintp_rx_uds_msg(
    LinTpEngine* engine,
    uint8_t* out_data_ptr,
    size_t out_data_cap,
    size_t* out_data_len
);

int32_t lintp_pop_error(LinTpEngine* engine, int32_t* out_error_code);

#ifdef __cplusplus
}
#endif

#endif /* ISOTP_ENGINE_H */
