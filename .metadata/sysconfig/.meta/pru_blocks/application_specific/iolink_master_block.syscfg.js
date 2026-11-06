"use strict";
/**
 * IO-Link Master Block
 *
 * Implements a complete IO-Link master transaction cycle (IEC 61131-9) for
 * the PRU-ICSS. Uses the ENDAT peripheral for hardware UART TX/RX framing
 * and the PRU GPIO for transceiver direction control and wake-up pulse
 * generation.
 *
 * COM rates: COM1=4.8k, COM2=38.4k, COM3=230.4k baud.
 * Frame format: IEC 61131-9 UART character, 11 bits: start, 8 data (LSB first), even parity, stop.
 * Half-duplex on C/Q line.
 *
 * The in-tool simulator does not model the ENDAT FIFO/TX/RX state machine, so
 * the transaction itself is not simulated (blockMaturity below).
 */

/**
 * Guarded .set definitions for the ICSS_CFG register symbols needed by the
 * ENDAT TX/RX wrapper (the same guarded symbols as uart_config.syscfg.js),
 * so the .asm file assembles even without explicit icss_cfg_regs.inc.
 */
function getIcssCfgDefinitions() {
    var s = "";
    s += "; ========== ICSS CFG register definitions (iolink_master_block) ==========\n";
    s += "    .if !$isdefed(\"ICSS_CFG\")\n";
    s += "    .asg    c4,     ICSS_CFG\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_GPCFG0\")\n";
    s += "ICSS_CFG_GPCFG0                 .set    0x0008\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_GPCFG1\")\n";
    s += "ICSS_CFG_GPCFG1                 .set    0x000C\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_PRU0_ENDAT_TXCFG\")\n";
    s += "ICSS_CFG_PRU0_ENDAT_TXCFG       .set    0x00E4\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_PRU1_ENDAT_TXCFG\")\n";
    s += "ICSS_CFG_PRU1_ENDAT_TXCFG       .set    0x0104\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_PRU0_ENDAT_RXCFG\")\n";
    s += "ICSS_CFG_PRU0_ENDAT_RXCFG       .set    0x00E0\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_PRU1_ENDAT_RXCFG\")\n";
    s += "ICSS_CFG_PRU1_ENDAT_RXCFG       .set    0x0100\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_PRU0_ENDAT_CH0_CFG0\")\n";
    s += "ICSS_CFG_PRU0_ENDAT_CH0_CFG0    .set    0x00E8\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_PRU0_ENDAT_CH1_CFG0\")\n";
    s += "ICSS_CFG_PRU0_ENDAT_CH1_CFG0    .set    0x00F0\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_PRU0_ENDAT_CH2_CFG0\")\n";
    s += "ICSS_CFG_PRU0_ENDAT_CH2_CFG0    .set    0x00F8\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_PRU1_ENDAT_CH0_CFG0\")\n";
    s += "ICSS_CFG_PRU1_ENDAT_CH0_CFG0    .set    0x0108\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_PRU1_ENDAT_CH1_CFG0\")\n";
    s += "ICSS_CFG_PRU1_ENDAT_CH1_CFG0    .set    0x0110\n";
    s += "    .endif\n";
    s += "    .if !$isdefed(\"ICSS_CFG_PRU1_ENDAT_CH2_CFG0\")\n";
    s += "ICSS_CFG_PRU1_ENDAT_CH2_CFG0    .set    0x0118\n";
    s += "    .endif\n";
    s += "; ========================================================================\n";
    return s;
}

/**
 * PRU number of the core this design is generated for (same helper as uart_config).
 * The ENDAT peripheral registers are per core, so the macro is emitted per core.
 */
function getPruNumberFromContext() {
    const common = system.getScript("/common");
    const coreName = common.getSelfSysCfgCoreName();
    if (coreName && coreName.includes("pru")) {
        const match = coreName.match(/pru(\d+)$/);
        if (match && match[1]) {
            const n = parseInt(match[1]);
            if (!isNaN(n)) {
                return n;
            }
        }
    }
    return 0;
}

// One IO-Link UART character: start bit, 8 data bits (LSB first), even parity bit, stop bit.
var FRAME_BITS = 11;
// CHn_CFG0: bitSwap [31], RX_FRAME_SIZE [27:16], TX_FRAME_SIZE [15:11].
var CHCFG0_VALUE = ((0x80000000 | (FRAME_BITS << 16) | (FRAME_BITS << 11)) >>> 0);
var cfg0Hex = "0x" + CHCFG0_VALUE.toString(16).toUpperCase();

function comModeToBaud(comMode) {
    switch (comMode) {
        case "COM1": return 4800;
        case "COM2": return 38400;
        case "COM3": return 230400;
        default:     return 230400;
    }
}

function baudToClockDiv(baud) {
    var clkSrc = 192000000;
    var rate = clkSrc / baud;
    // IO-Link tolerates ~2% baud rate error, so rounding to nearest integer is fine.
    var div = Math.round(rate) - 1;
    if (div < 0 || div > 65535) { return -1; }
    return div;
}

// RX clock divider for 8x oversampling: the receiver takes one FIFO sample per eighth of a bit.
function baudToRxClockDiv(baud) {
    var div = Math.round(192000000 / (baud * 8)) - 1;
    if (div < 0 || div > 65535) { return -1; }
    return div;
}

var iolinkModule = {
    displayName: "IO-Link Master",
    defaultInstanceName: "IOLINK_MSTR_",
    description: "IO-Link master PHY timing engine (IEC 61131-9).",

    longDescription: [
        "IO-Link master PHY engine that runs a complete master -> device",
        "transaction cycle on the C/Q half-duplex line.",
        "",
        "**Protocol flow per transaction:**",
        "  1. Wake-up pulse: C/Q driven low for >=80 us, then released high.",
        "  2. M-sequence TX: Master transmits N bytes (1..8), one 11-bit 8E1 character each, via ENDAT hardware.",
        "  3. Turnaround: C/Q released; master listens for >=4 bit periods.",
        "  4. Response RX: Device replies with M bytes (0..4), one 11-bit 8E1 character each, via ENDAT hardware.",
        "  5. Idle: C/Q high.",
        "",
        "**Peripheral resources:**",
        "- ENDAT channel (1 of 3 per PRU) for hardware UART TX/RX framing.",
        "- 1x R30 GPIO for transceiver direction control (DIR).",
        "- 1x R30 GPIO for wake-up pulse drive.",
        "- No IEP, MAC, or SPAD required.",
        "",
        "**Supported COM rates:**",
        "  COM1 = 4.8 kbps  (divider 39999, frame 11 bits)",
        "  COM2 = 38.4 kbps (divider  4999, frame 11 bits)",
        "  COM3 = 230.4 kbps(divider   832, frame 11 bits)",
        "",
        "**BOM:** Requires an external IO-Link transceiver (e.g. TI OL1013, MAX14819)",
        "to convert the PRU's 3.3V ENDAT UART to 24V C/Q levels.",
        "",
        '**Status:** metadata only. The ENDAT programming follows the UART blocks',
        '(`uart_config`, `uart_tx_op`, `uart_rx_op`), but the in-tool simulator does',
        "NOT model the internal ENDAT FIFO/TX/RX state machine, so the transaction",
        "is not simulated in the tool; only the R30 wake-up/DIR waveform is checked.",
        "The TI pru-simulator's peripheral-interface model runs the transmit and receive paths.",
        "The transmit path sends up to 8 bytes (two 32-bit inputs), one character per byte, each character",
        "started by its own TX GO.",
        "",
        "**Outputs:** output1 = response bytes, first received byte most significant (up to 4 bytes).",
        "output2 = receive status: bit 0 = parity error, bit 1 = framing error (start or stop bit), 0 = all",
        "characters were received correctly. The receiver is re-armed for every character and samples each",
        "bit at 8x oversampling.",
        "",
        "**Limits:** the character-to-character timing of a device response (characters following each",
        "other with 0..3 bit times of gap) and the ENDAT receiver's start-bit detection are checked only",
        "against the pru-simulator model, not on silicon. The DIR and Wake pins are R30 bits; while the",
        "ENDAT peripheral interface is selected, R30 byte 0 is the TX FIFO data byte, so pins in bits 0..7",
        "are also written as FIFO data. The pin mapping of the peripheral mode has to be confirmed on a board."
    ].join("\n"),

    uiView: "graph",

    config: [
        { name: "$shape",     hidden: true, default: "square" },
        { name: "$topLabel",  hidden: true, default: "" },
        {
            name: "blockMaturity", displayName: "Block Maturity", default: "metadata-only",
            options: [
                {name: "metadata-only"},
                {name: "assembles"},
                {name: "simulator-tested"},
                {name: "dual-oracle-tested"},
                {name: "hardware-validated"}
            ],
            hidden: true
        },
        {
            name: "comMode",
            displayName: "COM Mode",
            description: "IO-Link baud-rate mode (COM1=4.8k, COM2=38.4k, COM3=230.4k)",
            default: "COM3",
            options: [
                {name: "COM1", displayName: "COM1 (4.8 kbps)"},
                {name: "COM2", displayName: "COM2 (38.4 kbps)"},
                {name: "COM3", displayName: "COM3 (230.4 kbps)"}
            ]
        },
        {
            name: "baudDivider",
            displayName: "ENDAT Clock Divider",
            description: "Calculated ENDAT clock divider for the selected COM mode",
            default: 832,
            readOnly: true,
            getValue: function(inst) { return baudToClockDiv(comModeToBaud(inst.comMode)); }
        },
        {
            name: "baudHz",
            displayName: "Baud Rate (Hz)",
            description: "Actual baud rate in Hz for the selected COM mode",
            default: 230400,
            readOnly: true,
            getValue: function(inst) { return comModeToBaud(inst.comMode); }
        },
        {
            name: "txBytes",
            displayName: "M-Sequence TX Bytes",
            description: "Number of data bytes to transmit in the master frame (1..8, taken from the two 32-bit inputs, DATA_IN_LO byte 0 first). Each byte is sent as one 11-bit character.",
            default: 4,
            displayFormat: "dec"
        },
        {
            name: "rxBytes",
            displayName: "Response RX Bytes",
            description: "Number of data bytes expected in the device response (0..4, returned in output1, first byte most significant). 0 = receive phase skipped.",
            default: 4,
            displayFormat: "dec"
        },
        {
            name: "frameSizeBits",
            displayName: "Frame Size (bits)",
            description: "Per-byte frame bits (start + 8 data + even parity + stop = 11 for IO-Link, IEC 61131-9)",
            default: 11,
            readOnly: true,
            getValue: function() { return FRAME_BITS; }
        },
        {
            name: "bitPeriodNs",
            displayName: "Bit Period (ns)",
            description: "Calculated bit period from the selected COM rate",
            default: 4340,
            readOnly: true,
            getValue: function(inst) { return Math.round(1e9 / comModeToBaud(inst.comMode)); }
        },
        {
            name: "dirPin",
            displayName: "DIR Pin (R30 Bit)",
            description: "R30 GPIO bit to control transceiver direction (0=RX, 1=TX)",
            default: 2
        },
        {
            name: "wakePin",
            displayName: "Wake Pin (R30 Bit)",
            description: "R30 GPIO bit for wake-up pulse drive (driven low for >=80 us)",
            default: 3
        },
        {
            name: "endatChannel",
            displayName: "ENDAT Channel",
            description: "ENDAT peripheral channel for UART TX/RX (0, 1, or 2)",
            default: 0,
            options: [
                {name: 0, displayName: "Channel 0"},
                {name: 1, displayName: "Channel 1"},
                {name: 2, displayName: "Channel 2"}
            ]
        },
        {
            name: "wakeLoops",
            displayName: "Wake-up Delay Loops",
            description: "Number of delay-loop iterations for the wake-up pulse (minimum >=80 us). Each loop ~5 cycles @ 200 MHz = 25 ns.",
            default: 6400
        },
        {
            name: "turnaroundBitPeriods",
            displayName: "Turnaround (Bit Periods)",
            description: "Number of bit periods to wait between TX and RX for bus turnaround (minimum 4 per IO-Link spec)",
            default: 5
        },
        {
            name: "mSeqPreambleBytes",
            displayName: "M-Seq Preamble Bytes",
            description: "Number of preamble bytes to prefix before the M-sequence payload (1 = PREOPPER, 0 = OPERATE with no preamble)",
            default: 0,
            hidden: false
        },
        {
            name: "opCode",
            default: "m_iolink_master_pru0",
            hidden: true,
            getValue: function() { return "m_iolink_master_pru" + getPruNumberFromContext(); }
        },
        { name: "numOfInputPorts",   default: 2, hidden: true },
        { name: "numOfOutputPorts",  default: 2, hidden: true },
        { name: "multiOutputMacroArgs", default: true, hidden: true },
        { name: "output1Size",       default: 4, hidden: true },
        { name: "output2Size",       default: 4, hidden: true },
        { name: "numOfConstants",    default: 10, hidden: true },
        { name: "constant1", default: 2, hidden: true, getValue: i => i.dirPin },
        { name: "constant2", default: 3, hidden: true, getValue: i => i.wakePin },
        { name: "constant3", default: 0, hidden: true, getValue: i => i.endatChannel },
        { name: "constant4", default: 832, hidden: true, getValue: i => i.baudDivider },
        { name: "constant5", default: 4, hidden: true, getValue: i => i.txBytes },
        { name: "constant6", default: 4, hidden: true, getValue: i => i.rxBytes },
        { name: "constant7", default: 5, hidden: true, getValue: i => i.turnaroundBitPeriods },
        { name: "constant8", default: 6400, hidden: true, getValue: i => i.wakeLoops },
        { name: "constant9", default: 0, hidden: true, getValue: i => i.mSeqPreambleBytes },
        { name: "constant10", default: 103, hidden: true, getValue: i => baudToRxClockDiv(comModeToBaud(i.comMode)) },
        {
            name: "noOfCycles",
            displayName: "Number Of Cycles",
            getValue: function(inst) {
                var wake  = inst.wakeLoops         || 4000;
                var txB   = inst.txBytes           || 4;
                var rxB   = inst.rxBytes           || 4;
                var turn  = inst.turnaroundBitPeriods || 5;
                var baud  = comModeToBaud(inst.comMode || "COM3");
                var bitCy = 200e6 / baud;
                return (wake * 5) + 55 + (txB * 2) + 55 + (rxB * 15) + (turn * bitCy) + 20;
            },
            default: 50000
        }
    ],

    ports: function(inst) {
        return [
            { name: "prev",    type: "IO_PREV" },
            { name: "next",    type: "IO_NEXT" },
            { name: "input1",  type: "input32" },
            { name: "input2",  type: "input32" },
            { name: "output1", type: "output32" },
            { name: "output2", type: "output32" }
        ];
    },

    validate: function(inst, validation) {
        if (inst.txBytes < 1 || inst.txBytes > 8) {
            validation.logError("txBytes must be 1..8 (the two 32-bit inputs carry 8 payload bytes)", inst, "txBytes");
        }
        if (inst.rxBytes < 0 || inst.rxBytes > 4) {
            validation.logError("rxBytes must be 0..4 (the response is returned in one 32-bit output)", inst, "rxBytes");
        }
        if (inst.dirPin === inst.wakePin) {
            validation.logError("DIR pin and Wake pin must differ", inst, "dirPin");
        }
        if (inst.dirPin < 0 || inst.dirPin > 15) {
            validation.logError("dirPin must be 0..15", inst, "dirPin");
        }
        if (inst.wakePin < 0 || inst.wakePin > 15) {
            validation.logError("wakePin must be 0..15", inst, "wakePin");
        }
        if (inst.wakeLoops < 1600) {
            validation.logWarning(
                "wakeLoops=" + inst.wakeLoops + " may be too short. Minimum 3200 (~80 us @ 200 MHz, ~5 cy/loop) recommended.",
                inst, "wakeLoops"
            );
        }
        if (inst.turnaroundBitPeriods < 4) {
            validation.logError("Turnaround must be at least 4 bit periods per IO-Link spec", inst, "turnaroundBitPeriods");
        }
        var div = baudToClockDiv(comModeToBaud(inst.comMode));
        if (div < 0 || div > 65535) {
            validation.logError("Computed clock divider " + div + " out of range (0-65535)", inst, "comMode");
        }
    },

    // Build macro assembly by joining lines.
    // Uses only basic string concatenation to avoid template literal pitfalls.
    getMacro: function(pruInstructionMacro, opCode) {
        // The ENDAT configuration registers differ per core; the core is part of the macro name
        // ("m_iolink_master_pru0" / "m_iolink_master_pru1"). Without a core suffix PRU0 is used.
        var callParts = (pruInstructionMacro || "").trim().split(/\s*,\s*|\s+/);
        var macroName = callParts[0] || opCode || "m_iolink_master_pru0";
        var pru = macroName.indexOf("_pru1") >= 0 ? "1" : "0";
        var gpcfgReg = "ICSS_CFG_GPCFG" + pru;
        var txcfgReg = "ICSS_CFG_PRU" + pru + "_ENDAT_TXCFG";
        var rxcfgReg = "ICSS_CFG_PRU" + pru + "_ENDAT_RXCFG";
        var chCfgReg = function(ch) { return "ICSS_CFG_PRU" + pru + "_ENDAT_CH" + ch + "_CFG0"; };
        var busyBit = { "0": 5, "1": 13, "2": 21 };
        var lines = [];
        // Emit the per-channel variants of a code fragment as an assembler .if/.elseif/.else chain.
        var perChannel = function(emit) {
            for (var ch = 0; ch < 3; ch++) {
                lines.push(ch === 0 ? "    .if CHANNEL == 0" : (ch === 1 ? "    .elseif CHANNEL == 1" : "    .else"));
                emit(String(ch)).forEach(function(l) { lines.push(l); });
            }
            lines.push("    .endif");
        };

        // ---- macro header ----
        lines.push(macroName + " .macro RES, STATUS, DATA_IN_LO, DATA_IN_HI, DIR_PIN, WAKE_PIN, CHANNEL, CLK_DIV, TX_BYTES, RX_BYTES, TURN_BITS, WAKE_LPS, PREAMBLE, RX_CLK_DIV");
        lines.push("    ; ================================================================");
        lines.push("    ; m_iolink_master -- IO-Link master transaction engine");
        lines.push("    ;");
        lines.push("    ; Performs one complete IO-Link master cycle:");
        lines.push("    ;   WAKE -> ENDAT TX (M-sequence) -> TURNAROUND -> ENDAT RX (response)");
        lines.push("    ;");
        lines.push("    ; Parameters:");
        lines.push("    ;   RES         -- Destination register for received response bytes (first byte most significant)");
        lines.push("    ;   STATUS      -- Receive status: bit 0 = parity error, bit 1 = framing error (start/stop bit)");
        lines.push("    ;   DATA_IN_LO  -- Register with lower 32 bits of TX payload");
        lines.push("    ;   DATA_IN_HI  -- Register with upper 32 bits of TX payload");
        lines.push("    ;   DIR_PIN     -- R30 bit for transceiver direction (0=RX, 1=TX)");
        lines.push("    ;   WAKE_PIN    -- R30 bit for wake-up pulse (driven low for WAKE_LPS)");
        lines.push("    ;   CHANNEL     -- ENDAT channel (0/1/2)");
        lines.push("    ;   CLK_DIV     -- ENDAT TX clock divider (1x baud rate)");
        lines.push("    ;   TX_BYTES    -- Number of payload bytes to TX (1..8)");
        lines.push("    ;   RX_BYTES    -- Number of bytes expected from device (0..4, 0=skip RX)");
        lines.push("    ;   TURN_BITS   -- Turnaround pause in bit periods (min 4)");
        lines.push("    ;   WAKE_LPS    -- Wake-up delay loop iterations (~5 cy/iter @ 200 MHz)");
        lines.push("    ;   PREAMBLE    -- Preamble byte value (0 = no preamble)");
        lines.push("    ;   RX_CLK_DIV  -- ENDAT RX clock divider (8x baud rate)");
        lines.push("    ; ================================================================");

        // ---- register setup ----
        lines.push("    ; ---- register setup ----");
        lines.push("    ldi   RES, 0");
        lines.push("    ldi   STATUS, 0");
        lines.push("    ldi   TEMP_REG1, 0");
        lines.push("    ldi   TEMP_REG2, 0");
        lines.push("    ldi   TEMP_REG3, 0");

        // ---- Phase 1: Wake-up pulse ----
        lines.push("    ; ======== Phase 1: Wake-up pulse ========");
        lines.push("    ; Enable TX driver");
        lines.push("    set   R30, R30, DIR_PIN");
        lines.push("    ; Drive WAKE_PIN low");
        lines.push("    clr   R30, R30, WAKE_PIN");
        lines.push("    ; Hold for WAKE_LPS iterations (~5 cycles each = 25 ns)");
        lines.push("    ldi   TEMP_REG1.w0, WAKE_LPS");
        lines.push("m_iolink_wait_wake?:");
        lines.push("    sub   TEMP_REG1.w0, TEMP_REG1.w0, 1");
        lines.push("    qbne  m_iolink_wait_wake?, TEMP_REG1.w0, 0");
        lines.push("    ; Release wake pin high");
        lines.push("    set   R30, R30, WAKE_PIN");
        lines.push("    ; Disable TX driver (C/Q floats, pull-up holds high)");
        lines.push("    clr   R30, R30, DIR_PIN");
        lines.push("    ; Brief idle before TX (10 loops ~ 250 ns)");
        lines.push("    ldi   TEMP_REG1.w0, 10");
        lines.push("m_iolink_post_wake?:");
        lines.push("    sub   TEMP_REG1.w0, TEMP_REG1.w0, 1");
        lines.push("    qbne  m_iolink_post_wake?, TEMP_REG1.w0, 0");

        // ---- Phase 2: ENDAT TX configuration ----
        lines.push("    ; ======== Phase 2: Configure ENDAT TX ========");
        lines.push("    ; Enable the transceiver driver and select peripheral mode first. Once the peripheral");
        lines.push("    ; interface is active an R30 write also reaches the TX FIFO of the selected channel, so");
        lines.push("    ; the global reinit below comes after the R30 writes above and flushes what they pushed.");
        lines.push("    set     R30, R30, DIR_PIN");
        lines.push("    ldi     R30.b3, 0x00");
        lines.push("    ; Set PRU" + pru + " peripheral mode via GPCFG" + pru);
        lines.push("    ldi32   TEMP_REG1, 0x04000000");
        lines.push("    ldi     TEMP_REG2.w0, " + gpcfgReg);
        lines.push("    sbco    &TEMP_REG1, ICSS_CFG, TEMP_REG2.w0, 4");
        lines.push("    ; Global reinit (flushes the FIFOs), then poll reinit busy. R31 commands are written as a");
        lines.push("    ; byte field: R31 reads back the channel status in the bit positions the write decodes as");
        lines.push("    ; GO/reinit, so a read-modify-write (set R31, R31, n) could issue a command no one asked for.");
        lines.push("    ldi     R31.b2, 0x08                    ; reinit (R31 bit 19)");
        perChannel(function(ch) { return ["m_iolink_wait_reinit_tx?:", "    qbbs    m_iolink_wait_reinit_tx?, R31, " + busyBit[ch]]; });
        lines.push("    ldi     R30.b3, 0x00");
        lines.push("    ; Configure TX clock: [31:16]=CLK_DIV, [4]=0 (192 MHz UART_CLK)");
        lines.push("    ldi     TEMP_REG1.w0, 0");
        lines.push("    ldi     TEMP_REG1.w2, CLK_DIV");
        lines.push("    ldi     TEMP_REG2.w0, " + txcfgReg);
        lines.push("    sbco    &TEMP_REG1, ICSS_CFG, TEMP_REG2.w0, 4");
        lines.push("    ; CHCFG0: [31]=bitSwap (LSB first), [27:16]=RX frame size, [15:11]=TX frame size,");
        lines.push("    ; both " + FRAME_BITS + " (start + 8 data + parity + stop); TX is single-shot, one character per GO");
        lines.push("    ldi32   TEMP_REG1, " + cfg0Hex);
        perChannel(function(ch) { return ["    ldi     TEMP_REG2.w0, " + chCfgReg(ch)]; });
        lines.push("    sbco    &TEMP_REG1, ICSS_CFG, TEMP_REG2.w0, 4");
        lines.push("    ; Select channel with clk_mode=3");
        perChannel(function(ch) { return ["    ldi     R30.w2, 0x001" + (8 + parseInt(ch)).toString(16).toUpperCase()]; });

        // ---- Phase 3: transmit one UART character per payload byte ----
        lines.push("    ; ======== Phase 3: Transmit M-Sequence ========");
        lines.push("    ; One 11-bit character per payload byte, byte 0 = DATA_IN_LO.b0 first (up to 8 bytes):");
        lines.push("    ;   bit 0 = start (0), bits 8:1 = data LSB first, bit 9 = even parity, bit 10 = stop (1)");
        lines.push("    ; The frame is built in a full register and pushed as two FIFO bytes (low byte first);");
        lines.push("    ; with bitSwap set the low bit of each FIFO byte leaves first.");
        for (var k = 0; k < 8; k++) {
            var src = (k < 4 ? "DATA_IN_LO.b" + k : "DATA_IN_HI.b" + (k - 4));
            if (k > 0) { lines.push("    .if TX_BYTES >= " + (k + 1)); }
            lines.push("    mov     TEMP_REG1, " + src + "           ; data byte, zero-extended");
            lines.push("    lsr     TEMP_REG2, TEMP_REG1, 4         ; even parity: xor-fold the data byte");
            lines.push("    xor     TEMP_REG2, TEMP_REG2, TEMP_REG1");
            lines.push("    lsr     TEMP_REG3, TEMP_REG2, 2");
            lines.push("    xor     TEMP_REG2, TEMP_REG2, TEMP_REG3");
            lines.push("    lsr     TEMP_REG3, TEMP_REG2, 1");
            lines.push("    xor     TEMP_REG2, TEMP_REG2, TEMP_REG3");
            lines.push("    and     TEMP_REG2, TEMP_REG2, 1         ; parity bit (1 when the data has an odd number of ones)");
            lines.push("    lsl     TEMP_REG2, TEMP_REG2, 9");
            lines.push("    lsl     TEMP_REG1, TEMP_REG1, 1         ; data to bits 8:1, start bit 0 stays 0");
            lines.push("    or      TEMP_REG1, TEMP_REG1, TEMP_REG2");
            lines.push("    set     TEMP_REG1, TEMP_REG1, 10        ; stop bit");
            lines.push("    mov     R30.b0, TEMP_REG1.b0            ; FIFO byte 0: frame bits 7:0");
            lines.push("    mov     R30.b0, TEMP_REG1.b1            ; FIFO byte 1: frame bits 10:8");
            lines.push("    ldi     R31.b2, 0x04                    ; TX GO (R31 bit 18)");
            perChannel(function(ch) { return ["m_iolink_wait_tx" + k + "?:", "    qbbs    m_iolink_wait_tx" + k + "?, R31, " + busyBit[ch]]; });
            if (k > 0) { lines.push("    .endif ; TX_BYTES >= " + (k + 1)); }
        }
        lines.push("    ; Disable TX driver");
        lines.push("    clr     R30, R30, DIR_PIN");

        // ---- Phase 5: Turnaround ----
        lines.push("    ; ======== Phase 5: Turnaround ========");
        lines.push("    ; Wait TURN_BITS bit periods via nested delay loop");
        lines.push("    ; Inner: ~174 iterations per bit (calibrated for COM3 @ 230.4k)");
        lines.push("    ldi     TEMP_REG1.w0, TURN_BITS");
        lines.push("    ldi     TEMP_REG2.w0, 174");
        lines.push("m_iolink_turn_check?:");
        lines.push("    sub     TEMP_REG1.w0, TEMP_REG1.w0, 1");
        lines.push("    qbeq    m_iolink_turn_done?, TEMP_REG1.w0, 0");
        lines.push("    mov     TEMP_REG4.w0, TEMP_REG2.w0");
        lines.push("m_iolink_turn_inner?:");
        lines.push("    sub     TEMP_REG4.w0, TEMP_REG4.w0, 1");
        lines.push("    qbne    m_iolink_turn_inner?, TEMP_REG4.w0, 0");
        lines.push("    qbne    m_iolink_turn_check?, TEMP_REG1.w0, 1");
        lines.push("m_iolink_turn_done?:");
        lines.push("    nop");
        lines.push("    nop");

        // ---- Phase 6: ENDAT RX ----
        lines.push("    ; ======== Phase 6: Configure and Receive ENDAT RX ========");
        lines.push("    .if RX_BYTES > 0");
        lines.push("    ; ENDAT reinit for RX");
        lines.push("    ldi     R30.b3, 0x00");
        lines.push("    ldi     R31.b2, 0x08                    ; reinit (R31 bit 19)");
        perChannel(function(ch) { return ["m_iolink_wait_reinit_rx?:", "    qbbs    m_iolink_wait_reinit_rx?, R31, " + busyBit[ch]]; });
        lines.push("    ldi     R30.b3, 0x00");
        lines.push("    ; RXCFG: [31:16]=RX_CLK_DIV, [4]=0 (192 MHz UART_CLK), [3]=0 (start bit is low),");
        lines.push("    ; [2:0]=7 (8x oversampling: one FIFO sample per eighth of a bit)");
        lines.push("    ldi     TEMP_REG1.w0, 7");
        lines.push("    ldi     TEMP_REG1.w2, RX_CLK_DIV");
        lines.push("    ldi     TEMP_REG2.w0, " + rxcfgReg);
        lines.push("    sbco    &TEMP_REG1, ICSS_CFG, TEMP_REG2.w0, 4");
        lines.push("    ; CHCFG0: same value as for TX (RX_FRAME_SIZE [27:16] = " + FRAME_BITS + " bits)");
        lines.push("    ldi32   TEMP_REG1, " + cfg0Hex);
        perChannel(function(ch) { return ["    ldi     TEMP_REG2.w0, " + chCfgReg(ch)]; });
        lines.push("    sbco    &TEMP_REG1, ICSS_CFG, TEMP_REG2.w0, 4");
        lines.push("    ; One UART character per iteration: re-arm RX (rx_en low then high resets the frame");
        lines.push("    ; counter and flags), then read " + FRAME_BITS + " bit samples (start .. stop), LSB first.");
        lines.push("    ; The receiver delivers one FIFO sample per sampling period, so a bit is one sample; the");
        lines.push("    ; sample of bit 4 of the oversampling window is the middle of the bit cell.");
        lines.push("    ldi     TEMP_REG3.b0, RX_BYTES");
        lines.push("m_iolink_rx_next_byte?:");
        lines.push("    ldi     R30.b3, 0x00");
        perChannel(function(ch) { return ["    ldi     R30.b3, " + (1 << parseInt(ch))]; });
        lines.push("    ldi     TEMP_REG1, 0                    ; bit accumulator, new bits enter at bit 31");
        lines.push("    ldi     TEMP_REG4.b0, 0                 ; samples read");
        lines.push("m_iolink_rx_bit?:");
        perChannel(function(ch) { return [
            "    qbbc    m_iolink_rx_bit?, R31, " + (24 + parseInt(ch)) + "              ; valid flag of the channel",
            "    mov     TEMP_REG2.b0, R31.b" + ch + "           ; sample window of the channel",
            "    ldi     R31.b3, " + (1 << parseInt(ch)) + "                    ; clear the valid flag (write 1)"]; });
        lines.push("    lsr     TEMP_REG1, TEMP_REG1, 1");
        lines.push("    qbbc    m_iolink_rx_zero?, TEMP_REG2.b0, 4");
        lines.push("    set     TEMP_REG1, TEMP_REG1, 31");
        lines.push("m_iolink_rx_zero?:");
        lines.push("    add     TEMP_REG4.b0, TEMP_REG4.b0, 1");
        lines.push("    qbgt    m_iolink_rx_bit?, TEMP_REG4.b0, " + FRAME_BITS);
        lines.push("    ; Align: bit 0 = start, bits 8:1 = data, bit 9 = parity, bit 10 = stop");
        lines.push("    lsr     TEMP_REG1, TEMP_REG1, " + (32 - FRAME_BITS));
        lines.push("    ; Framing: start must be 0, stop must be 1 (STATUS bit 1)");
        lines.push("    qbbc    m_iolink_rx_start_ok?, TEMP_REG1, 0");
        lines.push("    or      STATUS, STATUS, 2");
        lines.push("m_iolink_rx_start_ok?:");
        lines.push("    qbbs    m_iolink_rx_stop_ok?, TEMP_REG1, 10");
        lines.push("    or      STATUS, STATUS, 2");
        lines.push("m_iolink_rx_stop_ok?:");
        lines.push("    ; Parity: data and parity bit together must hold an even number of ones (STATUS bit 0)");
        lines.push("    lsr     TEMP_REG4, TEMP_REG1, 1");
        lines.push("    clr     TEMP_REG4, TEMP_REG4, 9         ; data bits and parity bit, without the stop bit");
        lines.push("    lsr     TEMP_REG2, TEMP_REG4, 8");
        lines.push("    xor     TEMP_REG4, TEMP_REG4, TEMP_REG2");
        lines.push("    lsr     TEMP_REG2, TEMP_REG4, 4");
        lines.push("    xor     TEMP_REG4, TEMP_REG4, TEMP_REG2");
        lines.push("    lsr     TEMP_REG2, TEMP_REG4, 2");
        lines.push("    xor     TEMP_REG4, TEMP_REG4, TEMP_REG2");
        lines.push("    lsr     TEMP_REG2, TEMP_REG4, 1");
        lines.push("    xor     TEMP_REG4, TEMP_REG4, TEMP_REG2");
        lines.push("    qbbc    m_iolink_rx_parity_ok?, TEMP_REG4, 0");
        lines.push("    or      STATUS, STATUS, 1");
        lines.push("m_iolink_rx_parity_ok?:");
        lines.push("    ; Data byte into RES, first received byte in the most significant position");
        lines.push("    lsr     TEMP_REG1, TEMP_REG1, 1");
        lines.push("    and     TEMP_REG1, TEMP_REG1, 0xFF");
        lines.push("    lsl     RES, RES, 8");
        lines.push("    or      RES, RES, TEMP_REG1");
        lines.push("    sub     TEMP_REG3.b0, TEMP_REG3.b0, 1");
        lines.push("    qbne    m_iolink_rx_next_byte?, TEMP_REG3.b0, 0");
        lines.push("    ; Disable RX");
        lines.push("    ldi     R30.b3, 0x00");
        lines.push("    .endif ; RX_BYTES > 0");

        // ---- Phase 7: Cleanup ----
        lines.push("    ; ======== Phase 7: Cleanup ========");
        lines.push("    clr     R30, R30, DIR_PIN");
        lines.push("    ldi     R31.b2, 0x08                    ; reinit (R31 bit 19)");
        lines.push("    ldi     TEMP_REG1.w0, 20");
        lines.push("m_iolink_cleanup_delay?:");
        lines.push("    sub     TEMP_REG1.w0, TEMP_REG1.w0, 1");
        lines.push("    qbne    m_iolink_cleanup_delay?, TEMP_REG1.w0, 0");
        lines.push("    ldi     R30.b3, 0x00");
        lines.push("    .endm");

        return getIcssCfgDefinitions() + "\n" + lines.join("\n");
    },

    moduleStatic: {
        getPinUsage: function(inst) {
            const pins = [];
            pins.push({ pin: parseInt(inst.dirPin), dir: "GPO" });
            pins.push({ pin: parseInt(inst.wakePin), dir: "GPO" });
            return pins;
        },
        modules: function(inst) {
            return [{
                name: "pru_register_allocator_validator",
                moduleName: "/pru_blocks/common/pru_blocks_static_module"
            }];
        },
    },

    templates: {
        "/pru_blocks/common/pru_syscfg.asm.xdt": null,
        "/pru_blocks/common/pru_syscfg.inc.xdt": null,
    }
};

exports = iolinkModule;
