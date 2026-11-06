---

## SDFM Block (Sigma-Delta Filter Module)

### Purpose

The SDFM block retrieves digital current or voltage measurements from a Sigma-Delta Filter Module peripheral. In motor control applications, it allows the PRU to pull filtered feedback samples directly into its real-time control loop.

### Features

- Accesses any of the 4 hardware SDFM filter channels (0 to 3)
- Configurable Over Sampling Ratio (OSR) to control filtering depth and latency
- Yields a 32-bit filtered measurement to input/output data ports
- High-efficiency MMIO reads mapping to the SoC peripheral register space

### Configuration

| Parameter | Description | Default | Options |
|-----------|-------------|---------|---------|
| Filter Channel | The hardware SDFM channel to query | `0` | `0`, `1`, `2`, `3` |
| Output Port | The 32-bit destination register | `output1` | Automatic register |

### How It Works

The block reads one 32-bit result register per channel. It does not configure the SDFM filter: the oversampling ratio and the other filter settings are not part of the block and must be set up elsewhere.
$$\text{Address} = \texttt{0x0C080000} + (\text{Channel} \times 4)$$
The PRU issues one load from this address (the whole macro takes 10 cycles) and outputs the data. The base address and the per-channel stride are assumptions made in the macro, not values taken from a register map; check them against the device documentation before relying on the block on hardware.

### Important Notes

- In simulation, reading from the SDFM block utilizes the `addMmioReadHook` function to query the mocked register space.
- Physical transceiver and line settling delays are not simulated.

### Example Usage

To measure phase current in a motor controller:
1. Drag an SDFM block into the workspace.
2. Select the channel matching your hardware phase current sensor (e.g. Channel 1).
3. Connect the output of the SDFM block to the input of a Clarke/Park transform block.

---
