# AGENTS.md — Klipper GUI Project Context

## 1. Project Purpose

This repository contains a custom PyQt6 desktop application for controlling a laboratory bioprinting / multi-axis Klipper machine.

The application is currently developed on Windows and is intended to run on a Raspberry Pi 4 with Raspberry Pi OS Desktop 64-bit.

The GUI communicates with Klipper primarily through Moonraker's HTTP API. It also includes STL loading, 3D visualization, slicing/export functionality, protocol management, temperature controls, sterilization timers, and multi-origin printing for well plates.

Before changing code, inspect the repository and identify the actual entry point, module layout, configuration files, and current implementation. Do not assume filenames that are not present in the repository.

---

## 2. Primary Development Rules

1. Preserve existing working behavior unless the requested task explicitly requires changing it.
2. Make small, reviewable changes.
3. Before editing, inspect the relevant files and trace the complete call path.
4. After editing:
   - run syntax checks;
   - run available tests;
   - show a concise summary;
   - show the relevant diff.
5. Do not silently remove features, exception handling, safety checks, comments, or configuration entries.
6. Do not replace a working implementation with a large rewrite unless necessary.
7. Use `pathlib.Path` for filesystem paths.
8. Avoid Windows-only paths, APIs, shell commands, and assumptions.
9. The target platform is Raspberry Pi OS 64-bit on ARM64.
10. The Raspberry Pi has 2 GB RAM, so avoid unnecessary memory copies, large retained meshes, duplicate previews, and blocking UI operations.
11. Long-running work must not block the Qt main thread.
12. Use Qt signals/slots or worker threads for network, slicing, file processing, and other expensive work.
13. Keep user-facing errors clear and actionable.
14. Do not invent hardware pin assignments or machine dimensions. Use the values documented below or inspect the latest configuration file.
15. For Klipper configuration changes, validate pin reuse and section compatibility before editing.

---

## 3. Target Runtime

### Host

- Raspberry Pi 4
- RAM: 2 GB
- OS target: Raspberry Pi OS Desktop 64-bit
- Python 3
- PyQt6
- Moonraker
- Klipper
- PyVista
- PyVistaQt / `QtInteractor`

### Recommended Linux environment

The project should be compatible with a virtual environment created with system packages visible:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
```

Likely runtime dependencies include:

```text
PyQt6
requests
pyserial
pyvista
pyvistaqt
```

Confirm dependencies from the repository before creating or editing `requirements.txt`.

### Raspberry Pi compatibility checks

Search for and replace or isolate:

- absolute Windows paths such as `C:\...`
- `COM1`, `COM3`, and other Windows serial-port assumptions
- `os.startfile`
- `win32api`, `win32com`, `wmi`, or similar Windows-only modules
- backslash-only path handling
- case-insensitive filename assumptions
- Windows shell commands
- Windows-specific process handling

Prefer:

```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
```

Linux serial devices may appear as:

```text
/dev/ttyUSB0
/dev/ttyACM0
```

---

## 4. GUI Functional Areas

The application contains or is expected to contain the following functional areas.

### 4.1 Sterilization

- UV timer
- HEPA timer
- Timer range: 1–120 minutes
- Default: 10 minutes
- Uses `QSpinBox`
- Manual typing inside the spin box must remain usable and should not be disrupted by minimum-value enforcement
- Timers tick once per second
- Display format: `mm:ss`

### 4.2 Protocol Management

- Protocol list
- Open
- Edit
- Delete
- Protocol storage under:

```text
data/protocols
```

- JSON-based storage
- Editing state previously used a variable similar to:

```python
_editing_protocol_name
```

- Preserve backward compatibility with existing protocol JSON files where practical

### 4.3 Build Platform

Supported platform types include:

- Petri dish, approximately Ø60 mm
- Well plate
  - 6-well
  - 12-well
- Glass slide

Well plate UI requirements:

- Rows labeled A, B, C as appropriate
- Numbered columns
- Clickable/selectable wells
- Informational label updates when platform or selection changes

### 4.4 Multi-Well / Multi-Origin Printing

The user can select wells such as:

```text
A1 A2 A3 B1
```

One model copy should be printed at each selected well.

Known implementation concepts:

- Selected wells stored in protocol JSON under a field similar to:

```text
bp_selected_wells
```

- Viewport draws well hitboxes and selected states
- Exporter offsets model coordinates per selected well
- Inter-well Z lift: 2 mm
- Ordering is layer-major
- One shared G-code header
- One shared G-code footer
- Bed-boundary validation before export
- Atomic file writes
- Previously used default `origin_x` near 120 mm
- Example verified regions:
  - A1 approximately X76–86
  - A2 approximately X115–125

Do not change coordinate mapping without verifying the platform geometry and existing implementation.

### 4.5 Model View

- STL file opening
- PyVista-based 3D visualization
- PyVistaQt `QtInteractor`
- Grid / plate visualization
- Model preview
- Shadows, lighting, ambient settings, and colors may already be customized

Be careful with Raspberry Pi GPU/OpenGL limitations.

The GUI should fail gracefully when 3D rendering cannot initialize. Avoid crashing the entire application because of a rendering backend problem.

### 4.6 Settings

Known settings include:

- Printhead 1
- Printhead 2
- Printhead 3
- Checkable printhead selection
- Temperature control
- Layer thickness
- Print speed
- Grid type
- Grid distance
- Printhead temperature
- Platform temperature
- Slice
- Save

### 4.7 Print Controls

- Print
- Pause
- Stop
- Elapsed time
- Remaining time

### 4.8 Preview

- Cura-like layer slider
- Active layer should be visually placed on or very near the build plate
- A previous defect showed a selected layer floating in the air
- A flattening adjustment was implemented so the active layer is rendered around plate height, approximately Z=0.04–0.05
- Preserve this behavior unless the preview coordinate system is intentionally redesigned

---

## 5. Slicing and G-code Export

Known behavior and prior improvements:

### Exporter

- Bed-boundary validation
- Atomic output write
- X/Y origin offsets
- Default `origin_x` previously around 120 mm
- Per-well placement
- 2 mm Z lift between wells
- Layer-major output ordering
- Single header
- Single footer

### Slicer worker

- Cooperative abort/cancel
- Memory cleanup
- Avoid holding unnecessary mesh and slice copies

### Important constraints

- Do not freeze the GUI while slicing
- Cancellation should be cooperative and safe
- Partial or failed exports must not overwrite a previously valid file
- Validate generated G-code before starting a print
- Preserve user-visible error reporting

---

## 6. Moonraker Integration

The GUI uploads and starts G-code through Moonraker.

Known endpoints:

```text
/server/files/upload
/printer/print/start
```

Start-print payload resembles:

```json
{
  "filename": "example.gcode"
}
```

Known implementation requirements:

- Retry/backoff for transient failures
- Visible banner or user-facing error message on failure
- Do not claim success before Moonraker confirms the operation
- Use request timeouts
- Handle connection refusal, timeout, malformed JSON, HTTP errors, and Moonraker error responses
- Keep network calls outside the Qt main thread

When Moonraker runs on the same Raspberry Pi:

```text
http://127.0.0.1:7125
```

Do not hard-code this without first checking how the repository stores settings.

### Temperature mapping used by the GUI

Existing conceptual mapping:

```text
platform-temp  -> bed_cooler
printhead-temp -> peltier_1 / peltier_2 / peltier_3
```

Confirm the latest Klipper object names before editing.

---

## 7. Klipper Machine Overview

This is a custom multi-motor Cartesian machine.

### Controller

- MKS Monster8 v2.0
- Board variant referenced as `v2.0_002`
- Raspberry Pi 4 host
- TMC2208 drivers

### Kinematics

```text
cartesian
```

### Motion limits

Latest known values:

```ini
max_velocity: 150
max_accel: 2000
max_z_velocity: 15
max_z_accel: 30
square_corner_velocity: 5.0
```

### Axis ranges

```text
X: 0–230 mm
Y: 0–120 mm
Main Z: 0–83 mm
Manual gantry heads z1/z2: 0–82 mm
```

The user specifically wants z1 and z2 to remain within 0–82 mm.

---

## 8. Known Stepper and Endstop Configuration

Always inspect the latest Klipper configuration before modifying these values.

### X

```text
step_pin: PC14
dir_pin: !PC13
enable_pin: !PC15
endstop_pin: ^!PA14
rotation_distance: 4
microsteps: 16
full_steps_per_rotation: 200
position_max: 230
```

### Y

```text
step_pin: PE1
dir_pin: PE0
enable_pin: !PE2
endstop_pin: ^!PA15
rotation_distance: 4
position_max: 120
```

### Main Z

```text
step_pin: PE5
dir_pin: PE4
enable_pin: !PC15
endstop_pin: ^!PB11
rotation_distance: 2
position_endstop: 83
position_max: 83
second_homing_speed: 3
homing_positive_dir: true
```

Important: X and Z both historically referenced `!PC15` as enable. Verify the actual board mapping and latest config before changing anything. Do not assume duplicated pins are valid.

### Manual gantry stepper z1

```text
step_pin: PB5
dir_pin: PB4
enable_pin: !PB6
endstop_pin: ^!PE8
velocity: 5
accel: 100
position range: 0–82
```

### Manual gantry stepper z2

```text
step_pin: PD6
dir_pin: PD5
enable_pin: !PD7
endstop_pin: ^!PE9
velocity: 5
accel: 100
position range: 0–82
```

### Resin feed manual steppers

#### recine_1

```text
step_pin: PD2
dir_pin: !PD1
enable_pin: !PD3
endstop_pin: ^!PE15
rotation_distance: 0.8
```

#### recine_2

```text
step_pin: PC7
dir_pin: !PC6
enable_pin: !PC8
endstop_pin: ^!PD9
rotation_distance: 0.8
```

#### recine_3

```text
step_pin: PD13
dir_pin: !PD12
enable_pin: !PD7
endstop_pin: ^!PE11
rotation_distance: 0.8
```

Potential conflict: z2 and recine_3 have both referenced `!PD7` as enable in prior configuration data. This must be checked against the latest config and board schematic.

---

## 9. TMC2208 UART Configuration

Latest known current values:

### X

```ini
[tmc2208 stepper_x]
uart_pin: PE6
run_current: 0.8
hold_current: 0.3
stealthchop_threshold: 9999
```

### Y

```ini
[tmc2208 stepper_y]
uart_pin: PB7
run_current: 0.9
hold_current: 0.3
stealthchop_threshold: 9999
```

### Main Z

```ini
[tmc2208 stepper_z]
uart_pin: PE3
run_current: 0.9
hold_current: 0.5
stealthchop_threshold: 0
```

### Extruder / repurposed channel

```ini
[tmc2208 extruder]
uart_pin: PD0
run_current: 0.6
hold_current: 0.5
```

### z1

```ini
[tmc2208 manual_stepper z1]
uart_pin: PB3
run_current: 0.9
```

The exact Klipper section syntax must be checked in the current config.

### z2

```ini
[tmc2208 manual_stepper z2]
uart_pin: PD4
run_current: 0.9
```

### Known TMC problem

A prior recurring error was:

```text
Unable to read tmc uart 'stepper_z' register IFCNT
```

This persisted even after swapping X and Z drivers.

Do not treat this as conclusively solved. Check:

- UART pin mapping
- jumper configuration
- driver type
- driver orientation
- shared UART wiring
- board revision
- Klipper section name
- physical continuity
- driver address assumptions

---

## 10. Homing and Gantry Logic

Known desired homing sequence:

1. Home main Z
2. Home / align gantry heads z1 and z2
3. Home X and Y

A prior override concept was:

```text
G28 Z
KAFALARI_SIFIRLA or Z_GANTRY_HOMING
G28 X Y
```

`safe_z_home` was commented out in one revision.

### Gantry homing behavior

Known logic:

- `SET_POSITION=-18` was used to extend search travel
- Motion command remained limited to the real maximum
- When endstop is reached, position is assigned to 82 mm
- Endstop stopping mode should use:

```text
STOP_ON_ENDSTOP=home
```

The user explicitly prefers `STOP_ON_ENDSTOP=home`.

Do not revert to deprecated or ambiguous forms such as:

```text
STOP_ON_ENDSTOP=1
```

### Jogging

- Jog macros exist for:
  - 10 mm
  - 5 mm
  - 1 mm
  - 0.5 mm
  - 0.1 mm
- Guard checks should prevent motion outside 0–82 mm for z1/z2
- Use consistent jog speeds

---

## 11. Endstop History

A prior fault caused z1 and z2 to appear triggered while not physically pressed.

Original problematic pins included:

```text
PB12
PB13
```

These were associated with interference/conflict involving an Arduino load-cell signal.

The fix was:

```text
z1 endstop -> ^!PE8
z2 endstop -> ^!PE9
```

These were verified as:

```text
open when idle
TRIGGERED when pressed
```

A later test showed all of the following as triggered when physically engaged:

```text
manual_stepper z1: TRIGGERED
manual_stepper z2: TRIGGERED
stepper_x: TRIGGERED
stepper_y: TRIGGERED
stepper_z: TRIGGERED
```

---

## 12. Load Cell / Probe Status

Main Z load-cell probe previously used:

```text
pin: ^!PA13
speed: 3.0
z_offset: 0
```

Current known status:

```text
The load cell is not working.
```

Treat the load-cell feature as unresolved.

Before changing related code/configuration, inspect:

- current wiring
- signal voltage compatibility
- whether PA13 is still assigned elsewhere
- pull-up/inversion
- Arduino interface behavior
- debouncing/filtering
- Klipper probe state
- whether the load cell is intended as a digital trigger or analog measurement

Do not enable automated moves that rely on the load cell until the trigger can be verified safely.

---

## 13. Heater, Thermistor, Peltier, and Fan Context

### Heated bed

Known earlier configuration:

```text
heater_pin: PB10
sensor_pin: PC0
sensor_type: NTC 100K MGB18-104F39050L32
PID:
  kp: 71.039
  ki: 2.223
  kd: 567.421
```

Minimum temperature changed during revisions. A safer later value was near:

```text
min_temp: -10
```

Verify current hardware and config.

### Peltier modules

Three channels are planned.

#### Peltier 1

```text
heater/output pin: PB1
thermistor pin: PC1 / TH0
fan pin in one mapping: PA0
PID:
  kp: 26.213
  ki: 1.304
  kd: 131.721
```

#### Peltier 2

```text
heater/output pin: PB0
thermistor pin: PC2 / TH1
fan pin in one mapping: PA1
```

#### Peltier 3

```text
heater/output pin: PA3
thermistor pin: PC3
fan pin in one mapping: PA2
```

### Board fan headers

Known physical mapping discussed:

```text
FAN0: PA2
FAN1: PA1
FAN2: PA0
```

There was a mismatch between logical names and physical headers during testing. Do not rely only on section names.

### Latest Peltier test context

At the time of the latest field test:

- only Peltier 1 was connected
- stated hardware:
  - FAN0 -> PA2
  - heater -> PB1
  - TH0 -> sensor
- command:

```text
SET_FAN_SPEED FAN=peltier_1_fan SPEED=1.0
```

did not start the expected fan

- another macro/command named similarly to:

```text
peltier_1_test
```

did work

Observed temperatures included:

```text
P1: approximately 27.7 °C rising to about 33.0 °C
P2/P3: approximately -43 °C
T0: approximately -66 °C
```

The very low unconnected readings were likely floating/unconnected sensor inputs, but this must be verified in hardware.

### Bidirectional requirement

The user wants both heating and cooling.

A standard Klipper heater output is not sufficient to reverse current through a Peltier module. True bidirectional control requires suitable hardware such as an H-bridge or other reversible power stage.

Do not imply that software alone can reverse Peltier polarity.

Safety requirements:

- output must default off
- define valid sensor ranges
- reject disconnected/floating sensors
- prevent simultaneous conflicting drive states
- include hardware current protection
- avoid driving a Peltier without confirmed temperature feedback
- verify MOSFET/H-bridge current and thermal ratings

---

## 14. Display

A Mini 12864 display with NeoPixels is present.

Known concept:

```text
neopixel pin: EXP1_6
chain_count: 3
```

EXP1 mappings exist in the Klipper configuration.

Inspect current mappings before editing.

---

## 15. Current Priority Issues

At the time this file was prepared, the important unresolved or active items were:

1. Deploy the PyQt6 GUI to Raspberry Pi 4.
2. Make the codebase fully Linux/Raspberry Pi compatible.
3. Confirm the actual Python entry point and dependency set.
4. Validate PyVista/PyVistaQt performance on the 2 GB Raspberry Pi.
5. Preserve Moonraker upload/start behavior.
6. The load cell currently does not work.
7. Peltier 1 fan command/pin mapping needs verification.
8. Only Peltier 1 is currently connected for initial testing.
9. Bidirectional Peltier heating/cooling requires appropriate reversible power hardware.
10. The `stepper_z` TMC UART IFCNT error may still be unresolved.
11. Check potential duplicate pin assignments in the latest Klipper configuration.
12. Keep z1/z2 movement range at 0–82 mm.
13. Preserve `STOP_ON_ENDSTOP=home`.
14. Avoid unsafe automated motion until endstops and load-cell behavior are confirmed.

---

## 16. Repository Inspection Checklist

When beginning work in this repository, perform these checks first:

```text
1. List the repository tree.
2. Identify the application entry point.
3. Locate requirements/dependency files.
4. Locate Moonraker client code.
5. Locate slicer and G-code exporter modules.
6. Locate PyVista/QtInteractor initialization.
7. Locate protocol JSON schema and examples.
8. Locate platform/well coordinate mapping.
9. Locate Raspberry Pi or deployment scripts.
10. Locate Klipper configuration files.
11. Search for absolute Windows paths.
12. Search for COM-port assumptions.
13. Search for blocking requests in UI callbacks.
14. Search for broad exception swallowing.
15. Search for hard-coded Moonraker URLs.
16. Search for duplicate GPIO/MCU pin assignments.
17. Run syntax checks before editing.
```

Suggested commands:

```bash
find . -maxdepth 3 -type f | sort
grep -RInE 'C:\\\\|COM[0-9]+|os\.startfile|win32|wmi' .
grep -RInE '127\.0\.0\.1:7125|/server/files/upload|/printer/print/start' .
python -m compileall .
```

Use repository-appropriate alternatives if `grep` or `find` are unavailable.

---

## 17. Validation Expectations

For Python changes:

```bash
python -m compileall .
```

If tests exist:

```bash
pytest
```

For formatting/linting, only use tools already configured in the repository unless the user explicitly asks to introduce new tooling.

For GUI changes, validate at least:

- application starts
- main window opens
- tabs load
- protocol storage path resolves
- STL dialog opens
- preview initializes or fails gracefully
- Moonraker settings load
- no Windows-only path error occurs
- no blocking call freezes the UI

For Raspberry Pi deployment, document:

- OS version
- Python version
- architecture
- Qt/PyQt6 version
- PyVista/VTK version
- exact startup command
- exact error output, if any

---

## 18. Safe Klipper Work Procedure

Before testing motion:

1. Confirm emergency-stop access.
2. Keep travel small and speed low.
3. Run:

```text
QUERY_ENDSTOPS
```

4. Verify each switch manually.
5. Confirm expected `open` and `TRIGGERED` states.
6. Confirm motor direction using a very small movement.
7. Do not home toward an unverified switch.
8. Do not rely on the non-working load cell.
9. Stop immediately on grinding, repeated clicking, or stalled motion.
10. Re-check current, wiring, direction, mechanical binding, and travel limits.

Before testing Peltier outputs:

1. Confirm sensor is connected and reading plausibly.
2. Confirm the physical output pin.
3. Test at low duty cycle.
4. Monitor current and device temperature.
5. Keep a hardware power disconnect available.
6. Do not leave the output unattended.
7. Do not use unconnected sensor channels for control.

---

## 19. First Codex Task

When Codex first opens this repository, begin with analysis only.

Recommended first instruction:

```text
Read AGENTS.md and inspect the entire repository without changing files.
Identify the real application entry point, architecture, dependencies,
Windows-specific incompatibilities, Moonraker integration points,
PyVista/PyVistaQt usage, slicing/export pipeline, protocol storage,
and any Raspberry Pi deployment blockers.

Then provide:
1. a concise architecture map,
2. a prioritized Raspberry Pi migration checklist,
3. all risky assumptions or missing information,
4. the exact files that should be changed first,
5. a proposed validation plan.

Do not edit any file until the analysis is complete.
```

---

## 20. Communication Style for This Project

The user expects:

- technically precise answers
- exact commands
- clear explanation of what will change
- no skipped steps
- no vague claims that something is fixed without verification
- careful review of pin assignments and motion limits
- full consideration of safety implications
- Turkish explanations when communicating with the user

Code, identifiers, commit messages, and technical documentation may remain in English unless the existing repository uses another convention.
