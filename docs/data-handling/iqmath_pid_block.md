---

## IQMath PID Block

### Purpose

The IQMath PID block implements a discrete Proportional-Integral-Derivative (PID) controller utilizing fixed-point math. It is designed to close feedback control loops (such as position, speed, or current) on the PRU at high frequencies.

### Features

- Q12 fixed-point gain configuration ($K_p$, $K_i$, $K_d$)
- Uses the PRU broadside hardware MAC multiplier for fast scaling execution
- Stores error history and the running integral in a per-instance state symbol in Data RAM (DMEM)
- The default generated macro has a counted 50-cycle latency, including the three MAC operand-latency NOPs.

### Configuration

| Parameter | Description | Default | Options |
|-----------|-------------|---------|---------|
| $K_p$ | Proportional feedback gain constant, entered as a decimal and emitted as Q12 (value x 4096) | `1.0` (4096) | Decimal number |
| $K_i$ | Integral feedback gain constant, entered as a decimal and emitted as Q12 | `0.1` (410) | Decimal number |
| $K_d$ | Derivative feedback gain constant, entered as a decimal and emitted as Q12 | `0.01` (41) | Decimal number |

The gains are not range-checked by the block. The state storage is not configurable: each instance uses its own `<instance name>_pid_state` symbol (16 bytes).

### How It Works

The block compares the Setpoint input against the Feedback input to calculate the tracking Error:
$$\text{Error}_k = \text{Setpoint}_k - \text{Feedback}_k$$
It then calculates the PID output terms using truncating Q12 fixed-point arithmetic:
$$\text{Proportional} = \text{Error}_k \times K_p$$
$$\text{Integral}_k = \text{Integral}_{k-1} + \text{Error}_k$$
$$\text{Integral term} = \text{Integral}_k \times K_i$$
$$\text{Derivative} = (\text{Error}_k - \text{Error}_{k-1}) \times K_d$$
$$\text{Output}_k = \text{Proportional} + \text{Integral term} + \text{Derivative}$$
All state variables ($\text{Integral}$ and $\text{Error}_{k-1}$) are stored in DMEM and loaded/stored at each execution step.

### Important Notes

- PID coefficients are specified as fixed-point integers scaled by $2^{12}$ ($4096 = 1.0$).
- Each PID instance keeps its state in its own `<instance name>_pid_state` symbol, so instances with different names do not share state.

### Example Usage

To implement a motor velocity control loop:
1. Connect target velocity (Setpoint) to `input1`.
2. Connect measured velocity from an encoder block (Feedback) to `input2`.
3. Configure the $K_p$, $K_i$, and $K_d$ gains via the SysConfig interface.
4. Connect the block's output directly to the input of a PWM or motor drive block.

---
