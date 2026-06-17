# QLC+ "Priority System" — Rebuild Specification

> Purpose: this document describes the **function-priority feature** as currently
> implemented on the `LouMayans/qlcplus` `master` branch, so it can be **re-implemented
> cleanly** on a fresh fork of `mcallegari/qlcplus`. It separates the real feature code
> from the noise, explains the design, lists every touch point, and documents the bugs in
> the current implementation so the rebuild can avoid them.
>
> The current branch was forked from upstream at commit `78c165e94`
> ("qmlui: if Scene preview is running, live edit from editor", 2024-09-03). Everything in
> `78c165e94..HEAD` is local work.

---

## 1. What the feature does (intent)

Vanilla QLC+ mixes the DMX output of all running functions using **HTP** (Highest Takes
Precedence) for intensity channels and **LTP** (Latest Takes Precedence) for everything
else, ordered by an internal *fader priority* (Auto / SimpleDesk / Override / etc.). There
is **no per-function, user-assignable priority**: you cannot say "Scene A should always
win over Scene B regardless of values."

This feature adds an integer **`priority`** to every `Function` (and to Virtual Console
widgets). At DMX mix time, **a higher priority number wins the channel outright**,
overriding HTP/LTP. Functions with equal priority fall back to the normal HTP/LTP
behaviour. Default priority is `0`, so a project with no priorities set behaves exactly
like stock QLC+ (backward compatible).

Concrete use case: assign a "always-on" override scene priority `10`; everything else stays
at `0`. While the override runs, its channels can't be pulled down by lower scenes even if
those scenes ask for higher intensity values.

Two **separate** features got tangled into the same branch (see §6). They are *optional*
and should be decided on independently:
- **VC Button "Restart" action** (re-trigger a function from the top instead of toggling off).
- **OSC `-1` "feedback-only" channel** + **"external 0 stops the function"** behaviour, to
  keep an external OSC controller's button LEDs in sync without feedback loops.

---

## 2. Signal vs. noise — what to actually port

The raw diff `78c165e94..HEAD` touches **158 files / ~3,500 insertions / ~10,500 deletions**.
**Almost all of it is noise.** Do **not** try to replay the diff. Only the files in §4–§6
matter. Ignore the following entirely when rebuilding:

| Noise | Why ignore |
|---|---|
| `ui/src/qlcplus_*.ts` (all translation files, ~208 lines each) | Auto-regenerated `<location>` line numbers from `lupdate` because the `.ui` files shifted. Regenerate with the normal translation tooling; never hand-edit. |
| `.history/*` | VS Code Local History editor auto-saves. Not source. |
| `resources/fixtures/**` (large deletions) | Unrelated fixture-library churn. |
| `.github/workflows/build.yml`, `*/CMakeLists.txt`, `*.pro`, `variables.cmake/pri`, `qmake2cmake.md`, `create-dmg-cmake.sh`, `platforms/macos/**`, `debian/changelog`, `README.md`, `ui/src/aboutbox.ui` | Build/CI/packaging drift, line-ending (CRLF) churn. |
| `qmlui/**` (incl. `vcanimation*`, `vcspeeddial*`, `VCSpeedDial*.qml`, `VCAnimation*.qml`) | The **QML UI was NOT modified for priority.** These massive deletions are line-ending/merge churn. The feature targets the **classic Qt Widgets UI only** (`ui/src`). |
| `engine/src/script*.{cpp,h}`, `scriptv4.*`, `scriptrunner.*` | Confirmed: zero priority code. Unrelated scripting churn. |
| `engine/src/mastertimer*`, `qlcinputsource.cpp` (debug only), `rdmprotocol.cpp`, `plugins/dmxusb/**`, `webaccess.cpp`, `main.cpp`, `app.{h,cpp}`, `chaserrunner.cpp`, `cuestack.cpp`, `doc.cpp`, `collection.cpp`, `cue.cpp`, `chaser.cpp` | Only contain `qDebug()` prefix-tag edits (e.g. `"[doc][]"`) — **no logic**. |
| Stray `qDebug()` lines everywhere | The whole branch is littered with debug prints. None are part of the feature. |

**Rule of thumb:** the real feature is ~18 files (see §3). Everything else is debug noise,
line-ending churn, or auto-generated.

---

## 3. Complete file list for the feature (the only files to change)

Engine (core — UI independent):
- `engine/src/function.h` / `function.cpp` — the `priority` property on every function.
- `engine/src/genericfader.h` / `genericfader.cpp` — carry priority into the write path.
- `engine/src/universe.h` / `universe.cpp` — per-channel priority arbitration (the core).
- `engine/src/scene.cpp` — propagate scene priority to its faders.
- `engine/src/efx.cpp` — propagate EFX priority to its fader.
- *(gap)* `engine/src/rgbmatrix.cpp`, `engine/src/cuestack.cpp` — **not** wired in the
  current branch; decide whether to include (see §5 + §7 bug #7).

Classic UI (to set/show priority):
- `ui/src/sceneeditor.h` / `sceneeditor.cpp` — Priority spin box in the Scene editor.
- `ui/src/efxeditor.h` / `efxeditor.cpp` / `efxeditor.ui` — Priority spin box in the EFX editor.
- `ui/src/functionmanager.cpp` — extra tree columns.
- `ui/src/functionstreewidget.cpp` — populate the columns + show hidden functions.
- `ui/src/virtualconsole/vcwidget.h` / `vcwidget.cpp` — `louPriority` on the VC widget base + XML.
- `ui/src/virtualconsole/vcslider.cpp` — slider asserts its priority when overriding.
- `ui/src/virtualconsole/vcsliderproperties.cpp` / `vcsliderproperties.ui` — Priority spin box.

Optional separate features (§6):
- `ui/src/virtualconsole/vcbutton.h` / `vcbutton.cpp` / `vcbuttonproperties.cpp` / `vcbuttonproperties.ui`
- `engine/src/inputpatch.h` / `inputpatch.cpp`, `inputoutputmap.h` / `inputoutputmap.cpp`
- `engine/src/qlcinputsource.cpp` (only if needed), `plugins/interfaces/qlcioplugin.h`
- `plugins/osc/osccontroller.h` / `osccontroller.cpp` / `oscpacketizer.cpp` / `oscplugin.cpp`

> Naming note: the current branch prefixes everything with **`lou`** (`louPriority`,
> `getLouPriority`, `setLouPriority`, `m_louPriority`, `m_channelLouPriority`). In a clean
> rebuild prefer neutral names: `priority` / `m_priority` (where it doesn't clash with the
> existing `GenericFader::m_priority`, the fader-priority enum — use `m_funcPriority` or
> `m_userPriority` there to disambiguate).

---

## 4. Core engine design (the important part)

### 4.1 `Function` gets a priority

`engine/src/function.h`:
- Add member `int m_priority = 0;` (current branch calls it `louPriority`, uninitialized in
  one ctor path — **initialize it to 0**).
- Add `Q_PROPERTY(int priority READ getPriority WRITE setPriority NOTIFY priorityChanged)`.
- Add signal `void priorityChanged(quint32 fid);`.
- Add XML key: `#define KXMLQLCFunctionPriority QString("Priority")`.

`engine/src/function.cpp`:
- Initialize to `0` in **both** constructors.
- `copyFrom()` must copy priority: `setPriority(function->getPriority());`.
- `setPriority(int)`: early-return if unchanged, set, `emit priorityChanged(m_id);`
  (the current branch also emits `nameChanged(m_id)` to force the tree to refresh — see
  bug note; a cleaner option is to have the tree listen to `priorityChanged`).
- `getPriority()` returns the int (drop the `?: 0` GCC-ism — see bug #6).
- **Persistence**: in `saveXMLCommon()` write
  `doc->writeAttribute(KXMLQLCFunctionPriority, QString::number(getPriority()));`
  In `loader()` read `int priority = attrs.value(KXMLQLCFunctionPriority).toInt();` and
  call `function->setPriority(priority);` after `setName`.

### 4.2 `Universe` — per-channel priority arbitration (the heart)

This is what actually enforces priority. Vanilla `Universe` keeps `m_preGMValues` (a
`QByteArray` of channel values). We add a **parallel array of the priority of whoever last
wrote each channel this tick**.

`engine/src/universe.h`:
```cpp
QVector<int> m_channelLouPriority;   // size UNIVERSE_SIZE; one entry per channel
```
And extend the three write signatures with an `int priority` parameter (default 0 keeps
existing callers compiling):
```cpp
bool write(int address, uchar value, int priority = 0, bool forceLTP = false);
bool writeMultiple(int address, quint32 value, int channelCount, int priority = 0);
bool writeBlended(int address, quint32 value, int channelCount, BlendMode blend, int priority = 0);
```
> The current branch added an extra `bool debug` arg to `writeMultiple` — drop it, it was a
> debugging crutch.

`engine/src/universe.cpp`:
- Constructor: `m_channelLouPriority.fill(SENTINEL, UNIVERSE_SIZE);`
- **Choose ONE sentinel** for "nobody has written this channel this tick" and use it
  **everywhere consistently**. The branch used `-1000` in most places but `0` in one
  `reset()` overload (bug #4). Recommend a named constant, e.g.
  `static const int NO_PRIORITY = INT_MIN;` (so even negative user priorities work).
- `processFaders()`: **at the start of every tick**, before iterating faders,
  `m_channelLouPriority.fill(NO_PRIORITY);` (resets ownership each frame). Then process
  faders as usual (`fader->write(this)`).
- `reset()`: `m_channelLouPriority.fill(NO_PRIORITY);`
- `reset(int address, int range)`: reset the slice to `NO_PRIORITY`. **Use a loop or
  `std::fill`, not `memset`** — `memset` only works for byte values, and the branch's
  `memset(..., 0, range * sizeof(ptr))` is doubly wrong (wrong fill value AND
  `sizeof(pointer)` instead of `sizeof(int)`). See bug #4.

**The arbitration rule**, applied in `write`, `writeMultiple`, and `writeBlended`:
```cpp
// reject any write whose priority is strictly lower than the current owner of the channel
if (priority < m_channelLouPriority.at(address))
    return false;
// ... otherwise perform the write, then record ownership:
m_channelLouPriority[address] = priority;
```
For `writeBlended` `NormalBlend`, the HTP test must only apply **between equal priorities**:
```cpp
case NormalBlend:
    if (priority < m_channelLouPriority.at(address))
        return false;                                   // higher priority already owns it
    else if ((m_channelsMask->at(address) & HTP)
             && value < currentValue
             && priority == m_channelLouPriority.at(address))
        return false;                                   // equal priority -> normal HTP
    break;
```
Net semantics:
- **Higher priority wins outright** (overrides HTP and LTP).
- **Equal priority** → falls back to stock behaviour (HTP for intensity/NormalBlend, LTP /
  last-writer for Override/Flashing/MaskBlend/Additive).
- **Lower priority** → write rejected.
- Because the ownership array resets each tick and the gate is a strict `<`, the *winning*
  value is independent of fader processing order (a later higher-priority fader overwrites
  and re-stamps ownership; a later lower-priority fader is rejected).

> **Important fix for `writeMultiple`**: the branch checks/stamps only the **first** address
> (`address`) for a multi-channel value but writes the value across `address+i`. Do the
> priority check **and** the ownership stamp **per channel `i`** inside the loop, so
> multi-byte channels (16/24-bit) arbitrate correctly.

> **Remove** the dead debugging block the branch left in `processFaders()` after
> `emit universeWritten(...)`: a C99 VLA `int arr[postGM.count()];`, an unused `QString`,
> and a `memcpy`. It is non-standard C++, allocates an unbounded stack array, and does
> nothing. See bug #5.

### 4.3 `GenericFader` — carry the function's priority into the universe

Each function writes DMX through one or more `GenericFader`s. The fader needs to know the
priority of the function that owns it, and pass it to the universe writes.

`engine/src/genericfader.h`:
```cpp
void setPriority2(int p);   // name to avoid clash with existing setPriority(FaderPriority)
int  priority2() const;
private:
    int m_userPriority;     // initialize to 0 in the ctor!
```
> The existing `GenericFader::m_priority` / `priority()` / `setPriority()` are the **fader
> priority enum** (Auto/Override/...). Do **not** reuse those names. The branch added
> `m_louPriority` / `louPriority()` / `setLouPriority()` for exactly this reason — keep them
> distinct.

`engine/src/genericfader.cpp`:
- **Initialize `m_userPriority` to `0` in the constructor initializer list.** (The branch
  forgot to — bug #1.)
- In `write(Universe*)`, pass the priority to every universe write:
```cpp
if (flags & FadeChannel::Override)
    universe->write(address, value, priority2(), true);
else if (flags & FadeChannel::Relative)
    universe->writeRelative(address, value, channelCount);     // relative ignores priority
else if (flags & FadeChannel::Flashing)
    universe->writeMultiple(address, value, channelCount, priority2());
else
    universe->writeBlended(address, value, channelCount, m_blendMode, priority2());
```
> **Do NOT** copy the branch's hack that **comments out** the zero-target channel cleanup in
> `write()`. See bug #3 — that is almost certainly the root cause of "lots of bugs". Keep the
> upstream cleanup intact.

### 4.4 Propagation — which functions stamp their faders

Every place a function requests/creates its `GenericFader`, call `fader->setPriority2(getPriority())`
right after `requestFader(...)`:

| Function type | File / method | In branch? |
|---|---|---|
| Scene (DMX write) | `scene.cpp` `writeDMX()` | ✅ yes |
| Scene (per-value) | `scene.cpp` `processValue()` | ✅ yes |
| EFX | `efx.cpp` `getFader()` | ✅ yes |
| RGBMatrix | `rgbmatrix.cpp` `getFader()` | ❌ **missing** (bug #7) |
| CueStack (legacy VC cue list) | `cuestack.cpp` `getFader()`/`writeDMX()` | ❌ **missing** (bug #7) |
| Chaser / Collection / Sequence | — | n/a: they run **member** functions, which write through their own faders and so already carry their own priority. A chaser does not write DMX directly. |

For a clean rebuild, wire **all** fader-creating functions (Scene, EFX, RGBMatrix, CueStack)
so behaviour is consistent. With the `m_userPriority = 0` default (4.3), any function you
don't wire simply behaves as priority 0 — but **only if you fix bug #1**, otherwise it reads
garbage.

---

## 5. Classic-UI: setting & showing priority

### 5.1 Scene editor — `ui/src/sceneeditor.{h,cpp}`
- Add `QSpinBox *m_priorityEdit;`.
- In `init()`: create the spin box (range `0..100000`, step `1`), add a `"Priority:"` label
  and the spin box to the toolbar next to the name field.
- `m_priorityEdit->setValue(m_scene->getPriority());`
- `connect(m_priorityEdit, valueChanged(int), this, SLOT(slotPriorityEdited(int)));`
- `slotPriorityEdited(int)` → `m_scene->setPriority(value);`

### 5.2 EFX editor — `ui/src/efxeditor.{h,cpp,ui}`
- In `efxeditor.ui`, add a `QLabel "Priority"` and `QSpinBox m_louPriority` to the general
  page layout.
- In `initGeneralPage()`: set value from `m_efx->getPriority()`, range `0..100000`, step `1`,
  connect `valueChanged(int)` → `slotPriorityEdited(int)` → `m_efx->setPriority(value)`.

### 5.3 Function Manager tree — `ui/src/functionmanager.cpp` + `functionstreewidget.cpp`
The branch turns the single-column function tree into **3 columns**: `Function | Priority | Visible?`
and also **shows hidden functions** (it comments out the `isVisible()==false` early-returns).

In `functionmanager.cpp::initTree()`:
- `m_tree->setColumnCount(3);`
- `labels << tr("Function") << tr("Priority") << tr("Visible?");`

In `functionstreewidget.cpp`:
- Define columns. **Bug to avoid (#8):** the branch defines both
  `#define COL_PRIORITY 1` and `#define COL_PATH 1` — a collision. Use distinct indices:
  `COL_NAME 0`, `COL_PRIORITY 1`, `COL_VISIBLE 2`, and **move** `COL_PATH` to `3` (or store
  the path in `Qt::UserRole` data instead of a visible column).
- In `updateFunctionItem()` and the folder `parentItem()` branch, set
  `item->setText(COL_PRIORITY, QString::number(function->getPriority()));`
  `item->setText(COL_VISIBLE, QString::number(function->isVisible()));`
- (Optional) showing hidden functions is a separate UX choice — only comment out the
  `isVisible()==false` guards if you actually want hidden functions visible in the manager.

> The Priority column here is **read-only display**. Priority is *edited* in the Scene/EFX
> editors (5.1/5.2). There is currently no priority editor for RGBMatrix/Chaser/etc. in the
> UI — add one if you wire those types.

### 5.4 VC Widget base — `ui/src/virtualconsole/vcwidget.{h,cpp}`
- Add `int m_louPriority;` + `int louPriority() const;` + `void setLouPriority(int);`.
  **Initialize `m_louPriority = 0` in the constructor** (the branch forgot — bug #2).
- XML: define `#define KXMLQLCVCWidgetPriority QString("Priority")` (don't redefine the
  function macro). In `saveXMLCommon()`:
  `doc->writeAttribute(KXMLQLCVCWidgetPriority, QString::number(louPriority()));`
  In `loadXMLCommon()`:
  `setLouPriority(attrs.value(KXMLQLCVCWidgetPriority).toInt());`

### 5.5 VC Slider — `ui/src/virtualconsole/vcslider.cpp` + properties
- Properties dialog (`vcsliderproperties.ui` + `.cpp`): add a `"Priority"` label + spin box
  (`0..100000`); load from `m_slider->louPriority()`, save with `setLouPriority(value)` in `accept()`.
- In `writeDMXLevel()`, when the fader is (re)created/updated, set the fader's priority **only
  when the slider is actively overriding**:
  `fader->setPriority2(m_isOverriding ? louPriority() : -1);`
  Also re-apply it in the FadeChannel update loop and in `adjustIntensity()` (same expression).
  Rationale: a Level slider only asserts its priority while the user has it in "override"
  (red) state; otherwise it sits at `-1` so it loses to normal priority-0 functions.
  > Reconsider the `-1` sentinel in a clean build: if your channel "unwritten" sentinel is
  > `INT_MIN`, then a non-overriding slider at `-1` still beats truly-unwritten channels but
  > loses to any priority-0 function. Confirm that's the behaviour you want, or use `0`.

---

## 6. Optional, *separate* features bundled on this branch

These are **independent** of the priority system. Port them only if you want them; they are
the source of much of the late-branch churn ("button feedback with -1", "restart feature",
"send 0 from OSC").

### 6.A VC Button "Restart" action — `ui/src/virtualconsole/vcbutton.*`, `vcbuttonproperties.*`
- New `enum Action { Toggle, Flash, Blackout, StopAll, Restart };`
- XML token `#define KXMLQLCVCButtonActionRestart QString("Restart")`, handled in
  `actionToString()`/`stringToAction()`.
- Properties dialog: new `m_restart` radio button.
- In `pressFunction()`: treat `Restart` like `Toggle` for resolving the function, but **never
  stop-on-second-press** — always reset the intensity override and (re)start, so each press
  restarts the function from the top. (Behaves like Flash but latched: it keeps running until
  it naturally ends — useful for one-shot chasers/sequences.)
- `slotFunctionStopped()` treats `Restart` like `Toggle` (sets the button Inactive).

### 6.B "External 0 stops the function" (VC Button toggle)
In `vcbutton.cpp::slotInputValueChanged()`, the Toggle branch becomes:
- `value > 0 && state() == Inactive` → `pressFunction(); updateFeedback();`
- `value == 0 && state() == Active` → explicitly `f->stop(functionParent()); resetIntensityOverrideAttribute(); updateFeedback();`

So an external controller sending an explicit `0` turns the function **off** (instead of the
stock "any release" behaviour). This pairs with 6.C to avoid feedback loops.

### 6.C OSC `-1` "feedback-only" channel
A **second, parallel input signal path** named `valueFeedback` / `inputValueFeedback`,
distinct from the normal `valueChanged` / `inputValueChanged` path. Purpose: let an external
OSC controller push a value that **only updates the widget's feedback/LED state** without
triggering the widget's action — so you can resync controller LEDs without a feedback storm.

Mechanism (sentinel = OSC float `-1.0`):
1. `plugins/osc/oscpacketizer.cpp`: when decoding a float, if `fVal == -1` append `(char)(-1)`
   to the value bytes instead of `(char)(255.0 * fVal)`.
2. `plugins/osc/osccontroller.cpp` `handlePacket()`: for a single value, if `value == -1`
   emit `valueFeedback(...)`; otherwise emit `valueChanged(...)`. (New `valueFeedback` signal
   in `osccontroller.h`.) *(Branch quirk: multi-value arrays containing `-1` are silently
   dropped — fix to also emit feedback per element if you care.)*
3. `plugins/osc/oscplugin.cpp`: re-emit `valueFeedback` up from the controller.
4. `plugins/interfaces/qlcioplugin.h`: add `void valueFeedback(...)` signal to the plugin
   interface.
5. `engine/src/inputpatch.{h,cpp}`: add `slotValueFeedback()` + a separate
   `m_inputFeedbackBuffer` (with its own mutex), buffer feedback values, and **flush** them in
   `flush()` as `inputValueFeedback(...)`. Connect the plugin's `valueFeedback` → this slot in `set()`.
6. `engine/src/inputoutputmap.{h,cpp}`: add `inputValueFeedback(...)` signal; connect/forward
   from the input patch in `setInputPatch()`/disconnect path.
7. `engine/src/universe.{h,cpp}`: add `inputValueFeedback` signal + `slotInputValueFeedback`
   slot; wire it in `connectInputPatch()`/`disconnectInputPatch()` alongside `inputValueChanged`.
8. `ui/src/virtualconsole/vcwidget.{h,cpp}`: add virtual `slotInputValueFeedback(...)`; connect
   it everywhere `slotInputValueChanged` is connected in `setInputSource()`.
9. `ui/src/virtualconsole/vcbutton.{h,cpp}`: override `slotInputValueFeedback()` →
   if `checkInputSource(...)` matches, call `updateFeedback()` only (no press).

> The `-1` arrives as `uchar 255` in the normal path, so it is critical it's split off into
> the feedback path **before** reaching `slotInputValueChanged` (step 2 does this). Keep the
> `valueFeedback` path strictly feedback-only.

---

## 7. Known bugs in the current implementation (avoid these in the rebuild)

These are the most likely reasons "all my changes seem to make lots of bugs":

1. **`GenericFader::m_louPriority` uninitialized.** Not in the ctor initializer list. Faders
   whose function never calls `setLouPriority` (RGBMatrix, CueStack) read **garbage**, so
   arbitration is nondeterministic. → initialize to `0`.
2. **`VCWidget::m_louPriority` uninitialized.** Same problem for VC widgets. → initialize to `0`.
3. **Zero-target channel cleanup commented out** in `GenericFader::write()` (the
   "0 values still went through" commit). This leaves dead `FadeChannel`s alive forever, so
   faders never release channels / never self-delete → stuck values, functions that won't let
   go, HTP channels that never drop. **This is probably the biggest bug.** Solve the
   "priority-0 writes get lost" problem via the arbitration rule, **not** by disabling cleanup.
4. **`Universe::reset(address, range)` corrupts the priority array.**
   `memset(m_channelLouPriority.data()+address, 0, range * sizeof(ptr))` uses the wrong fill
   value (`0` vs the `-1000` sentinel used elsewhere) **and** `sizeof(pointer)` instead of
   `sizeof(int)` (buffer overrun). → use a typed fill loop / `std::fill` with the one true sentinel.
5. **Dead VLA debug block in `processFaders()`** (`int arr[postGM.count()]` + unused string +
   `memcpy`). Non-standard, unbounded stack alloc, no effect. → delete.
6. **`getLouPriority()` uses GCC `?:`** (`louPriority ?: 0`) — non-standard and a no-op. → `return m_priority;`.
7. **RGBMatrix and CueStack don't propagate priority** (their `getFader()` never calls
   `setLouPriority`). Combined with bug #1 they arbitrate on garbage. → wire them, or rely on a
   correct `0` default.
8. **Tree column index collision** in `functionstreewidget.cpp`: `COL_PRIORITY` and `COL_PATH`
   are both `1`. → give every column a distinct index.
9. **`writeMultiple` arbitrates on the first channel only** but writes/stamps per channel. →
   check and stamp ownership **per channel** inside the loop.
10. **Sentinel zoo.** The code mixes `-1000` (channel unwritten), `0` (default priority,
    and the buggy reset value), and `-1` (slider not-overriding, and the OSC feedback marker).
    These overlap confusingly. → pick named constants: a channel-unwritten sentinel distinct
    from any valid priority (`INT_MIN`), default priority `0`, and keep the OSC `-1` marker in
    the *input-value* domain only (never the priority domain).
11. **`setLouPriority` emits `nameChanged`** to force the tree to refresh. Works, but couples
    concerns. → have the tree connect to `priorityChanged` instead.
12. **Unused `int m_priority` field** added to the `Attribute` struct in `function.h` — dead. → drop.

---

## 8. Suggested clean rebuild order

1. **Engine core, no UI:**
   a. `Function`: add `m_priority(=0)`, getter/setter/signal, Q_PROPERTY, XML save+load (§4.1).
   b. `Universe`: add `m_channelLouPriority`, extend `write`/`writeMultiple`/`writeBlended`
      with `priority`, implement the arbitration rule, reset semantics with one sentinel,
      per-channel stamping (§4.2). Fix bugs #4, #5, #9.
   c. `GenericFader`: add `m_userPriority(=0)`, getter/setter, pass into universe writes,
      keep upstream cleanup (§4.3). Fix bugs #1, #3.
   d. Propagation in `scene.cpp`, `efx.cpp` (+ `rgbmatrix.cpp`, `cuestack.cpp` if wanted) (§4.4).
   → **Test headless:** two scenes on the same channels, different priorities; confirm higher
   wins regardless of value, equal falls back to HTP, save/reload preserves priority.
2. **Classic UI** to set/show it: Scene editor, EFX editor, function tree columns (§5.1–5.3).
3. **VC widget priority**: base class + slider (§5.4–5.5).
4. **Optional features** (§6), each as its own commit so they can be reverted independently:
   Restart action, external-0-stop, OSC `-1` feedback path.
5. Run the existing engine tests (`engine/test/`); note the branch modified
   `engine/test/chaser/chaser_test.{cpp,h}` — review whether those changes are real or noise
   before copying.

---

## 9. How to build (from the repo's `how to` file)

1. Open **MSYS2 MinGW 64-bit** shell (`C:\msys64` → `mingw64.exe`).
2. `cd` to the QLC+ build folder.
3. `make install`
4. Output installs to `C:\qlcplus`.

(QLC+ supports both qmake and CMake builds; this branch builds via MinGW `make install`.)

---

## 10. XML compatibility summary

- Function priority is stored as an attribute on the `<Function>` element:
  `<Function ID="…" Type="…" Name="…" Priority="N" …>`. Default/absent = `0`, so old
  workspaces load unchanged.
- VC widget priority is stored as a `Priority` attribute on the widget element (written in
  `saveXMLCommon`, read in `loadXMLCommon`). Default/absent = `0`.
- VC Button `Restart` action is stored as `Action="Restart"`.
- No new XML for the OSC feedback path (it's runtime-only).
