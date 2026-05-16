#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/time.h>

#include "loragw_hal.h"
#include "loragw_reg.h"
#include "loragw_sx1261.h"
#include "sx1261_defs.h"

struct mc_rx_packet_s {
    uint8_t payload[256];
    uint16_t size;
    uint8_t status;
    uint32_t freq_hz;
    uint8_t if_chain;
    uint8_t rf_chain;
    uint8_t modulation;
    uint8_t bandwidth;
    uint32_t datarate;
    uint8_t coderate;
    float rssic;
    float rssis;
    float snr;
    uint32_t count_us;
};

static uint32_t g_freq_hz = 0;
static uint32_t g_bw_hz = 125000;
static uint8_t g_bw_code = BW_125KHZ;
static uint8_t g_sf = DR_LORA_SF9;
static uint8_t g_cr = CR_LORA_4_5;
static int8_t g_power = 14;
static uint16_t g_preamble = 17;
static uint16_t g_sync_word = 0x3444;
static bool g_lorawan_public = false;
static bool g_no_header = false;
static bool g_invert = false;
static bool g_sx1261_enabled = false;
static bool g_sx1261_lora_rx_enabled = false;

#define SX1261_IRQ_RX_DONE 0x0002
#define SX1261_IRQ_PREAMBLE_DETECTED 0x0004
#define SX1261_IRQ_SYNCWORD_VALID 0x0008
#define SX1261_IRQ_HEADER_VALID 0x0010
#define SX1261_IRQ_HEADER_ERR 0x0020
#define SX1261_IRQ_CRC_ERR 0x0040
#define SX1261_IRQ_TIMEOUT 0x0200

struct mc_sx1261_debug_s {
    uint32_t receive_entry_count;
    uint32_t sx1261_branch_count;
    uint32_t sx1302_fallback_count;
    uint32_t poll_count;
    uint32_t rx_done_count;
    uint32_t crc_err_count;
    uint32_t timeout_count;
    uint32_t header_err_count;
    uint32_t preamble_count;
    uint32_t syncword_count;
    uint32_t header_valid_count;
    uint16_t last_irq_flags;
    uint8_t last_status;
    uint8_t last_rx_size;
    uint8_t last_rx_start;
    uint8_t lora_rx_enabled;
    uint8_t last_pkt_status[4];
};

static struct mc_sx1261_debug_s g_sx1261_debug;

static uint64_t monotonic_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return ((uint64_t)tv.tv_sec * 1000ULL) + ((uint64_t)tv.tv_usec / 1000ULL);
}

static uint8_t map_bw(uint32_t bw_hz) {
    /* BW_62K5 is patched into the project-local HAL copy by build_semtech_bridge.sh. */
    if (bw_hz <= 62500) return BW_62K5;
    if (bw_hz <= 125000) return BW_125KHZ;
    if (bw_hz <= 250000) return BW_250KHZ;
    return BW_500KHZ;
}

static uint8_t map_cr(uint8_t cr_den) {
    switch (cr_den) {
        case 5: return CR_LORA_4_5;
        case 6: return CR_LORA_4_6;
        case 7: return CR_LORA_4_7;
        case 8: return CR_LORA_4_8;
        default: return CR_LORA_4_5;
    }
}

static uint8_t map_sx1261_lora_bw(uint32_t bw_hz) {
    if (bw_hz <= 62500) return 0x03;
    if (bw_hz <= 125000) return 0x04;
    if (bw_hz <= 250000) return 0x05;
    return 0x06;
}

static int sx1261_configure_lora_rx(void) {
    uint8_t buff[16];
    int32_t freq_reg;
    uint16_t irq_mask = SX1261_IRQ_RX_DONE | SX1261_IRQ_PREAMBLE_DETECTED | SX1261_IRQ_SYNCWORD_VALID |
                        SX1261_IRQ_HEADER_VALID | SX1261_IRQ_HEADER_ERR | SX1261_IRQ_CRC_ERR | SX1261_IRQ_TIMEOUT;
    uint8_t ldro = (((uint64_t)1 << g_sf) * 1000ULL / g_bw_hz) >= 16 ? 1 : 0;

    /* SX1302 cannot demodulate 62.5 kHz LoRa RX in this HAL path, so the
       companion SX1261 is configured as the packet RX modem for that mode. */
    if (!g_sx1261_enabled) return LGW_HAL_ERROR;

    buff[0] = SX1261_STDBY_RC;
    if (sx1261_reg_w(SX1261_SET_STANDBY, buff, 1) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    if (sx1261_reg_w(SX1261_SET_FS, buff, 0) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    freq_reg = SX1261_FREQ_TO_REG(g_freq_hz);
    buff[0] = (uint8_t)(freq_reg >> 24);
    buff[1] = (uint8_t)(freq_reg >> 16);
    buff[2] = (uint8_t)(freq_reg >> 8);
    buff[3] = (uint8_t)(freq_reg >> 0);
    if (sx1261_reg_w(SX1261_SET_RF_FREQUENCY, buff, 4) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    buff[0] = SX1261_PACKET_TYPE_LORA;
    if (sx1261_reg_w(SX1261_SET_PACKET_TYPE, buff, 1) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    buff[0] = 0x00;
    buff[1] = 0x00;
    if (sx1261_reg_w(SX1261_SET_BUFFER_BASE_ADDRESS, buff, 2) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    buff[0] = g_sf;
    buff[1] = map_sx1261_lora_bw(g_bw_hz);
    buff[2] = g_cr;
    buff[3] = ldro;
    if (sx1261_reg_w(SX1261_SET_MODULATION_PARAMS, buff, 4) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    buff[0] = 0x07;
    buff[1] = 0x40;
    buff[2] = (uint8_t)(g_sync_word >> 8);
    buff[3] = (uint8_t)(g_sync_word >> 0);
    if (sx1261_reg_w(SX1261_WRITE_REGISTER, buff, 4) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    buff[0] = (uint8_t)(g_preamble >> 8);
    buff[1] = (uint8_t)(g_preamble >> 0);
    buff[2] = g_no_header ? 0x01 : 0x00;
    buff[3] = 0xFF;
    buff[4] = 0x01;
    buff[5] = g_invert ? 0x01 : 0x00;
    if (sx1261_reg_w(SX1261_SET_PACKET_PARAMS, buff, 6) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    buff[0] = (uint8_t)(irq_mask >> 8);
    buff[1] = (uint8_t)(irq_mask >> 0);
    buff[2] = 0x00;
    buff[3] = 0x00;
    buff[4] = 0x00;
    buff[5] = 0x00;
    buff[6] = 0x00;
    buff[7] = 0x00;
    if (sx1261_reg_w(SX1261_SET_DIO_IRQ_PARAMS, buff, 8) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    buff[0] = 0xFF;
    buff[1] = 0xFF;
    if (sx1261_reg_w(SX1261_CLR_IRQ_STATUS, buff, 2) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    buff[0] = 0xFF;
    buff[1] = 0xFF;
    buff[2] = 0xFF;
    if (sx1261_reg_w(SX1261_SET_RX, buff, 3) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;

    g_sx1261_lora_rx_enabled = true;
    return LGW_HAL_SUCCESS;
}

static int sx1261_receive_lora(struct mc_rx_packet_s *out_pkt) {
    uint8_t irq[3] = {0};
    uint8_t status[3] = {0};
    uint8_t pkt_status[4] = {0};
    uint8_t read_buf[258] = {0};
    uint16_t irq_flags;
    uint8_t size;
    uint8_t start;

    if (!g_sx1261_lora_rx_enabled || out_pkt == NULL) return 0;

    g_sx1261_debug.poll_count++;
    if (sx1261_reg_r(SX1261_GET_IRQ_STATUS, irq, 3) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;
    g_sx1261_debug.last_status = irq[0];
    irq_flags = ((uint16_t)irq[1] << 8) | irq[2];
    if (irq_flags == 0) return 0;
    g_sx1261_debug.last_irq_flags = irq_flags;
    if ((irq_flags & SX1261_IRQ_PREAMBLE_DETECTED) != 0) g_sx1261_debug.preamble_count++;
    if ((irq_flags & SX1261_IRQ_SYNCWORD_VALID) != 0) g_sx1261_debug.syncword_count++;
    if ((irq_flags & SX1261_IRQ_HEADER_VALID) != 0) g_sx1261_debug.header_valid_count++;
    if ((irq_flags & SX1261_IRQ_HEADER_ERR) != 0) g_sx1261_debug.header_err_count++;
    if ((irq_flags & SX1261_IRQ_CRC_ERR) != 0) g_sx1261_debug.crc_err_count++;
    if ((irq_flags & SX1261_IRQ_TIMEOUT) != 0) g_sx1261_debug.timeout_count++;
    if ((irq_flags & SX1261_IRQ_RX_DONE) != 0) g_sx1261_debug.rx_done_count++;

    uint8_t clear[2] = {(uint8_t)(irq_flags >> 8), (uint8_t)(irq_flags >> 0)};
    (void)sx1261_reg_w(SX1261_CLR_IRQ_STATUS, clear, 2);

    if ((irq_flags & (SX1261_IRQ_TIMEOUT | SX1261_IRQ_HEADER_ERR)) != 0) {
        (void)sx1261_configure_lora_rx();
        return 0;
    }
    if ((irq_flags & SX1261_IRQ_RX_DONE) == 0) return 0;

    if (sx1261_reg_r(SX1261_GET_RX_BUFFER_STATUS, status, 3) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;
    size = status[1];
    start = status[2];
    g_sx1261_debug.last_rx_size = size;
    g_sx1261_debug.last_rx_start = start;
    if (size == 0) {
        (void)sx1261_configure_lora_rx();
        return 0;
    }

    read_buf[0] = start;
    read_buf[1] = 0x00;
    if (sx1261_reg_r(SX1261_READ_BUFFER, read_buf, (uint16_t)size + 2) != LGW_REG_SUCCESS) return LGW_HAL_ERROR;
    (void)sx1261_reg_r(SX1261_GET_PACKET_STATUS, pkt_status, 4);
    memcpy(g_sx1261_debug.last_pkt_status, pkt_status, sizeof(g_sx1261_debug.last_pkt_status));

    memset(out_pkt, 0, sizeof(*out_pkt));
    out_pkt->size = size;
    out_pkt->status = (irq_flags & SX1261_IRQ_CRC_ERR) != 0 ? STAT_CRC_BAD : STAT_CRC_OK;
    out_pkt->freq_hz = g_freq_hz;
    out_pkt->if_chain = 0xFE;
    out_pkt->rf_chain = 0xFE;
    out_pkt->modulation = MOD_LORA;
    out_pkt->bandwidth = g_bw_code;
    out_pkt->datarate = g_sf;
    out_pkt->coderate = g_cr;
    out_pkt->rssic = -(float)pkt_status[1] / 2.0f;
    out_pkt->rssis = out_pkt->rssic;
    out_pkt->snr = (int8_t)pkt_status[2] / 4.0f;
    memcpy(out_pkt->payload, read_buf + 2, size);

    (void)sx1261_configure_lora_rx();
    return 1;
}

static void fill_sx1250_tx_lut(struct lgw_tx_gain_lut_s *lut) {
    static const int8_t rf_power[16] = {12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27};
    static const uint8_t pa_gain[16] = {0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1};
    static const uint8_t pwr_idx[16] = {15,16,17,19,20,22,1,2,3,4,5,6,7,9,11,14};
    memset(lut, 0, sizeof(*lut));
    lut->size = 16;
    for (int i = 0; i < 16; ++i) {
        lut->lut[i].rf_power = rf_power[i];
        lut->lut[i].dig_gain = 0;
        lut->lut[i].pa_gain = pa_gain[i];
        lut->lut[i].dac_gain = 3;
        lut->lut[i].mix_gain = 8;
        lut->lut[i].pwr_idx = pwr_idx[i];
    }
}

int mc_lgw_start(const char *spi_path, uint32_t freq_hz, uint32_t bw_hz, uint8_t sf,
                 uint8_t cr_den, int8_t tx_power, uint16_t preamble,
                 uint16_t sync_word, bool implicit_header, bool invert_iq, bool lorawan_public,
                 const char *sx1261_spi_path) {
    struct lgw_conf_board_s board;
    struct lgw_conf_rxrf_s rf;
    struct lgw_conf_demod_s demod;
    struct lgw_conf_rxif_s rxif;
    struct lgw_tx_gain_lut_s txlut;
    struct lgw_conf_ftime_s ftime;
    struct lgw_conf_sx1261_s sx1261;

    g_freq_hz = freq_hz;
    g_bw_hz = bw_hz;
    g_bw_code = map_bw(bw_hz);
    g_sf = sf;
    g_cr = map_cr(cr_den);
    g_power = tx_power;
    g_preamble = preamble ? preamble : 17;
    g_lorawan_public = lorawan_public;
    g_sync_word = sync_word ? sync_word : (g_lorawan_public ? 0x3444 : 0x1424);
    g_no_header = implicit_header;
    g_invert = invert_iq;
    g_sx1261_enabled = false;
    g_sx1261_lora_rx_enabled = false;
    memset(&g_sx1261_debug, 0, sizeof(g_sx1261_debug));

    lgw_stop();

    memset(&board, 0, sizeof(board));
    board.lorawan_public = lorawan_public;
    board.clksrc = 0;
    board.full_duplex = false;
    board.com_type = LGW_COM_SPI;
    snprintf(board.com_path, sizeof(board.com_path), "%s", spi_path ? spi_path : "/dev/spidev0.0");
    if (lgw_board_setconf(&board) != LGW_HAL_SUCCESS) return LGW_HAL_ERROR;

    memset(&rf, 0, sizeof(rf));
    rf.enable = true;
    rf.freq_hz = freq_hz;
    rf.rssi_offset = -166.0f;
    rf.rssi_tcomp.coeff_a = 0.0f;
    rf.rssi_tcomp.coeff_b = 0.0f;
    rf.rssi_tcomp.coeff_c = 0.0f;
    rf.rssi_tcomp.coeff_d = 0.0f;
    rf.rssi_tcomp.coeff_e = 0.0f;
    rf.type = LGW_RADIO_TYPE_SX1250;
    rf.tx_enable = true;
    rf.single_input_mode = false;
    if (lgw_rxrf_setconf(0, &rf) != LGW_HAL_SUCCESS) return LGW_HAL_ERROR;

    memset(&demod, 0, sizeof(demod));
    demod.multisf_datarate = LGW_MULTI_SF_EN;
    (void)lgw_demod_setconf(&demod);

    memset(&rxif, 0, sizeof(rxif));
    rxif.enable = true;
    rxif.rf_chain = 0;
    rxif.freq_hz = 0;
    rxif.bandwidth = g_bw_code;
    rxif.datarate = sf;
    rxif.sync_word_size = 0;
    rxif.sync_word = 0;
    rxif.implicit_hdr = implicit_header;
    rxif.implicit_payload_length = 0;
    rxif.implicit_crc_en = !implicit_header;
    rxif.implicit_coderate = g_cr;
    if (bw_hz != 62500) {
        /* MeshCore configures one exact LoRa channel, so use the standard modem
           instead of the multi-SF banks even for 125 kHz. */
        if (lgw_rxif_setconf(8, &rxif) != LGW_HAL_SUCCESS) return LGW_HAL_ERROR;
    }

    fill_sx1250_tx_lut(&txlut);
    if (lgw_txgain_setconf(0, &txlut) != LGW_HAL_SUCCESS) return LGW_HAL_ERROR;

    memset(&ftime, 0, sizeof(ftime));
    ftime.enable = false;
    if (lgw_ftime_setconf(&ftime) != LGW_HAL_SUCCESS) return LGW_HAL_ERROR;

    memset(&sx1261, 0, sizeof(sx1261));
    if (sx1261_spi_path != NULL && sx1261_spi_path[0] != '\0') {
        sx1261.enable = true;
        snprintf(sx1261.spi_path, sizeof(sx1261.spi_path), "%s", sx1261_spi_path);
        sx1261.rssi_offset = 0;
        sx1261.lbt_conf.enable = false;
        g_sx1261_enabled = true;
    } else {
        sx1261.enable = false;
    }
    if (lgw_sx1261_setconf(&sx1261) != LGW_HAL_SUCCESS) return LGW_HAL_ERROR;

    int start_ret = lgw_start();
    if (start_ret != LGW_HAL_SUCCESS) return start_ret;
    if (bw_hz == 62500 && g_sx1261_enabled) {
        return sx1261_configure_lora_rx();
    }
    return LGW_HAL_SUCCESS;
}

int mc_lgw_stop(void) {
    return lgw_stop();
}

int mc_lgw_send(const uint8_t *payload, uint16_t size) {
    struct lgw_pkt_tx_s pkt;
    if (payload == NULL || size > 255) return LGW_HAL_ERROR;
    memset(&pkt, 0, sizeof(pkt));
    pkt.freq_hz = g_freq_hz;
    pkt.tx_mode = IMMEDIATE;
    pkt.count_us = 0;
    pkt.rf_chain = 0;
    pkt.rf_power = g_power;
    pkt.modulation = MOD_LORA;
    pkt.freq_offset = 0;
    pkt.bandwidth = g_bw_code;
    pkt.datarate = g_sf;
    pkt.coderate = g_cr;
    pkt.invert_pol = g_invert;
    pkt.preamble = g_preamble;
    pkt.no_crc = false;
    pkt.no_header = g_no_header;
    pkt.size = size;
    memcpy(pkt.payload, payload, size);
    return lgw_send(&pkt);
}

int mc_lgw_receive(struct mc_rx_packet_s *out_pkt) {
    struct lgw_pkt_rx_s pkt;
    int ret;
    if (out_pkt == NULL) return LGW_HAL_ERROR;
    /* These counters cover all receive polling, not only SX1261 packet RX. */
    g_sx1261_debug.receive_entry_count++;
    g_sx1261_debug.lora_rx_enabled = g_sx1261_lora_rx_enabled ? 1 : 0;
    if (g_bw_hz == 62500 && g_sx1261_lora_rx_enabled) {
        g_sx1261_debug.sx1261_branch_count++;
        return sx1261_receive_lora(out_pkt);
    }
    g_sx1261_debug.sx1302_fallback_count++;
    memset(&pkt, 0, sizeof(pkt));
    ret = lgw_receive(1, &pkt);
    if (ret <= 0) return ret;
    memset(out_pkt, 0, sizeof(*out_pkt));
    out_pkt->size = pkt.size;
    out_pkt->status = pkt.status;
    out_pkt->freq_hz = pkt.freq_hz;
    out_pkt->if_chain = pkt.if_chain;
    out_pkt->rf_chain = pkt.rf_chain;
    out_pkt->modulation = pkt.modulation;
    out_pkt->bandwidth = pkt.bandwidth;
    out_pkt->datarate = pkt.datarate;
    out_pkt->coderate = pkt.coderate;
    out_pkt->rssic = pkt.rssic;
    out_pkt->rssis = pkt.rssis;
    out_pkt->snr = pkt.snr;
    out_pkt->count_us = pkt.count_us;
    if (pkt.size > 0 && pkt.size <= 256) {
        memcpy(out_pkt->payload, pkt.payload, pkt.size);
    }
    return 1;
}

int mc_lgw_status(uint8_t select, uint8_t *code) {
    return lgw_status(0, select, code);
}

int mc_lgw_spectral_scan_noise(uint32_t freq_hz, uint16_t nb_scan, int16_t *noise_dbm, uint16_t timeout_ms) {
    lgw_spectral_scan_status_t status = LGW_SPECTRAL_SCAN_STATUS_NONE;
    int16_t levels[LGW_SPECTRAL_SCAN_RESULT_SIZE];
    uint16_t results[LGW_SPECTRAL_SCAN_RESULT_SIZE];
    uint64_t deadline;
    /* Spectral scans borrow SX1261. In 62.5 kHz mode it must be restored to
       packet RX afterward; in 125 kHz mode SX1302 keeps receiving packets. */
    bool restore_lora_rx = (g_bw_hz == 62500 && g_sx1261_lora_rx_enabled);

    if (!g_sx1261_enabled || noise_dbm == NULL) return LGW_HAL_ERROR;
    if (nb_scan == 0) nb_scan = 200;
    if (timeout_ms == 0) timeout_ms = 2000;

    if (lgw_spectral_scan_start(freq_hz ? freq_hz : g_freq_hz, nb_scan) != LGW_HAL_SUCCESS) {
        if (restore_lora_rx) (void)sx1261_configure_lora_rx();
        return LGW_HAL_ERROR;
    }

    deadline = monotonic_ms() + timeout_ms;
    do {
        if (lgw_spectral_scan_get_status(&status) != LGW_HAL_SUCCESS) {
            (void)lgw_spectral_scan_abort();
            if (restore_lora_rx) (void)sx1261_configure_lora_rx();
            return LGW_HAL_ERROR;
        }
        if (status == LGW_SPECTRAL_SCAN_STATUS_COMPLETED) break;
        if (status == LGW_SPECTRAL_SCAN_STATUS_ABORTED || status == LGW_SPECTRAL_SCAN_STATUS_UNKNOWN) {
            if (restore_lora_rx) (void)sx1261_configure_lora_rx();
            return LGW_HAL_ERROR;
        }
        struct timeval sleep_tv = {0, 50 * 1000};
        select(0, NULL, NULL, NULL, &sleep_tv);
    } while (monotonic_ms() < deadline);

    if (status != LGW_SPECTRAL_SCAN_STATUS_COMPLETED) {
        (void)lgw_spectral_scan_abort();
        if (restore_lora_rx) (void)sx1261_configure_lora_rx();
        return LGW_HAL_ERROR;
    }
    if (lgw_spectral_scan_get_results(levels, results) != LGW_HAL_SUCCESS) {
        if (restore_lora_rx) (void)sx1261_configure_lora_rx();
        return LGW_HAL_ERROR;
    }

    for (int i = 0; i < LGW_SPECTRAL_SCAN_RESULT_SIZE; ++i) {
        if (results[i] > 0) {
            *noise_dbm = levels[i];
            if (restore_lora_rx) (void)sx1261_configure_lora_rx();
            return LGW_HAL_SUCCESS;
        }
    }
    if (restore_lora_rx) (void)sx1261_configure_lora_rx();
    return LGW_HAL_ERROR;
}

int mc_lgw_experimental_62k5_if_chain(void) { return -1; }

int mc_lgw_bandwidth_code(void) { return (int)g_bw_code; }

int mc_lgw_bandwidth_hz(void) { return (int)g_bw_hz; }

int mc_lgw_sx1261_debug(struct mc_sx1261_debug_s *out_debug) {
    if (out_debug == NULL) return LGW_HAL_ERROR;
    memcpy(out_debug, &g_sx1261_debug, sizeof(*out_debug));
    return LGW_HAL_SUCCESS;
}

const char *mc_lgw_version(void) {
    return lgw_version_info();
}
