# NMR Data processing software GUI and interaction architecture description

## 1. Document Purpose

This document records the GUI architecture, reconstruction and feature-integration plan for the existing NMR data processing software.

The goal is not to simply add a GUI control, but to create one:

- Concise
- Clear
- Streamlined
- State driven
- Data at the core
- Taking Pipeline as the core processing process
- Minimise windows and repetitive operations

Scientific research data processing software interface.

Before modifying an existing project, you must read and understand the existing project structure, module responsibilities, data flows and existing spectrum viewers. Do not re-implement existing functions to implement this document.

---

# 2. Core design ideas

The entire software uses:

> Select the object on the left -> The middle shows the functions that the current object can perform/Pipeline -> display the corresponding spectrum or result on the right

The three areas work around the same current context.

Core relationships:

```text
Project
  ↓
Experiment
  ↓
Data
  ↓
Pipeline
  ↓
Processing Step
  ↓
Output
```

GUI shouldn't be just a traditional "file browser + a bunch of buttons", but a visualization of the above data and processing status.

User should at any time be able to answer:

1. What project am I working on now?
2. Which experiment am I in now?
3. What set of data am I currently working with?
4. What steps have been completed with the current data?
5. What can be done next?
6. Which file does the current result correspond to?
7. If processing fails, why does it fail?

---

# 3. Overall GUI layout

Adopt three-column main interface:

```text
┌──────────────────────────────────────────────────────────────┐
│ File  Project  Process  View  Tools  Window  Settings  Help  │
├──────────────┬──────────────────────────┬────────────────────┤
│              │                          │                    │
│ Project tree │ Pipeline / actions       │ Spectrum viewer    │
│              │                          │                    │
│  Project     │1) Import data            │                    │
│   └Experiment│      ↓                   │    Spectrum        │
│      └Data   │2) Generate FID           │      Viewer        │
│        ├Input│      ↓                   │                    │
│        ├Proc │3) Data processing        │                    │
│        └Output      ↓                   │                    │
│              │4) Post-processing        │                    │
│              │                          │                    │
├──────────────┴──────────────────────────┴────────────────────┤
│ Task / Log / Progress                                         │
└──────────────────────────────────────────────────────────────┘
```

Recommended initial ratio:

- Left: 20–25%
- Middle: 40–45%
- Right: 30–35%

Three areas should support dragging to adjust width.

The Task/Log area at the bottom can be collapsed by default and will only be expanded when running tasks, viewing logs, or when errors occur.

---

# 4. Left: Project management tree

## 4.1 target

The left side is not a simple operating system file browser, but the software's own "logical project tree".

Recommended level:

```text
Project
├── Experiment
│   ├── Data
│   │   ├── Input
│   │   ├── Processing
│   │   ├── Output
│   │   └── Figures
│   └── Data
└── Experiment
```

For example:

```text
Project_A
├── Experiment_01
│   ├── Data_001
│   │   ├── Input
│   │   ├── Processing
│   │   ├── Output
│   │   └── Figures
│   └── Data_002
└── Experiment_02
```

The actual directory structure can be adjusted according to the existing project situation, but the logical hierarchy must be clear.

---

# 5. Objects in the project tree

At least distinguish between:

- Project
- Experiment
- Data
- Input
- Processing
- Output
- Figures
- Spectrum/spectrum file
- Processing Script (if existing projects really need to be exposed to users)

Different types of objects should have different icons and right-click menus.

---

# 6. The relationship between file folder and data object

Very important:

When a user clicks on any file folder on the left, the software must be able to determine:

```text
current object = the object the user clicked
current Data = the Data that object belongs to
current Experiment = the Experiment that Data belongs to
current Project = the Project that Experiment belongs to
```

For example:

```text
Project_A
└── Experiment_01
    └── Data_001
        └── Processing
```

User clicks `Processing`:

```text
Current Object = Processing
Current Data = Data_001
Current Experiment = Experiment_01
Current Project = Project_A
```

The intermediate Pipeline should still work around `Data_001`, rather than treating `Processing` as a completely independent data object.

---

# 7. Double-click behaviour on the left

Double-click file The clip can be entered/Expand the corresponding content.

But the core meaning of double-clicking is not to simply open the operating system file folder, but to:

> Update the current context and synchronize the middle and right regions

For example:

User click:

```text
Data_001
```

Shown in the middle:

```text
Data Dashboard
```

User click:

```text
Data_001/Processing
```

The middle still revolves around:

```text
Data_001
```

Display processing status and related operations.

---

# 8. Right-click menu

Provide different menus for different objects, do not use the same menu for all objects.

## Project

```text
Open project
New experiment
Project settings
Refresh
Export project
Open containing directory
Delete project
```

Deleting a project must explicitly prompt that the entire project directory may be deleted.

## Experiment

```text
Open
New data
Rename
Copy
Refresh
Open containing directory
Delete
```

## Data

```text
Open data
Import data
Continue processing
Reprocess
View spectrum
Open containing directory
Rename
Delete
```

## Input / Processing / Output / Figures

```text
Open
Open containing directory
Copy path
Refresh
Delete
```

Original input data should be deleted with caution by default, and it is best to add confirmation.

## Spectrum

```text
Open spectrum
Open in the spectrum viewer
Copy path
Open containing directory
Delete
```

---

# 9. Original data protection

The original data is scientific research data and cannot be processed like ordinary cache files.

Suggestion:

- Input / original data is read-only by default or at least confirmed twice when deleted
- Processing intermediate files can be deleted normally
- Software-generated Output can be deleted and regenerated
- Deletion of Project requires forced confirmation
- Don't let Pipeline overwrite original data by default

If an existing project already has a data protection mechanism, it should be reused first.

---

# 10. Behaviour after clicking data on the left

When the user selects a Data, the middle area enters:

## Data Dashboard

For example:

```text
Data_001

Data status

✓ Raw data
✓ FID
✓ Processed result
× SMILE Reconstruction
× Peak Picking
× Assignment

Pipeline

[Continue processing]
```

At the same time, the right side can automatically display the most relevant spectrum currently.

---

# 11. Behaviour after clicking on a high-level object

If user selects Project:

Show:

## Project Dashboard

Include:

- Project name
- Project path
- Number of experiments
- Data quantity
- Recent tasks
- Data processing completion status
- Quickly create Experiment/Data

For example:

```text
Project_A

Experiments: 3
Datasets: 12

Processing Status
Data_001   ██████████ 100%
Data_002   ███████░░░  70%
Data_003   ██░░░░░░░░  20%

[New experiment]
[New data]
```

---

# 12. Experiment Dashboard

After selecting Experiment, it displays:

```text
Experiment_01

Data
├── Data_001
├── Data_002
└── Data_003

Processing progress

Data_001   100%
Data_002    70%
Data_003    20%

[Batch processing]
```

Can provide:

- Data list
- Pipeline status
- Batch processing
- Experimental setup

---

# 13. Middle area: Pipeline

Pipeline is one of the core areas of the software.

Don't make Pipeline a set of ordinary buttons.

The ground floor should be designed to:

> Pipeline = a set of Processing Tasks with dependencies

For example:

```text
1) Import data
      ↓
2) Generate FID
      ↓
3) Data processing
      ↓
④ SMILE Reconstruction
      ↓
⑤ Peak Picking
      ↓
⑥ Assignment
      ↓
⑦ Figure Generation
```

The actual steps are subject to the functions that have been implemented in existing projects.

---

# 14. Pipeline status system

Each Pipeline Step must have a clear state.

It is recommended to support at least:

| Status | Meaning |
|---|---|
| LOCKED | The precondition is not met |
| READY | can run |
| RUNNING | Running |
| SUCCESS | Successfully |
| FAILED | Execution failed |
| OUTDATED | The upstream data has changed and needs to be rerun |
| CANCELLED | user cancel |

GUI can be used:

```text
✓ SUCCESS
▶ READY
🔒 LOCKED
… RUNNING
! OUTDATED
× FAILED
```

Icons and colors can be implemented according to the existing UI style, but the status must be clear.

---

# 15. Pipeline dependency checking

Subsequent steps cannot be judged by "whether the button is clicked" to determine whether it can run.

Should be based on:

> The status of the preceding Task + required output file + parameter / whether the input changes

Determine whether the current Task is executable.

For example:

```text
Import Data
    ↓
Generate FID
    ↓
Process Data
    ↓
Peak Picking
```

If FID does not exist:

```text
Generate FID = READY
Process Data = LOCKED
Peak Picking = LOCKED
```

If FID generation is complete:

```text
Generate FID = SUCCESS
Process Data = READY
Peak Picking = LOCKED
```

---

# 16. Must handle "result expiration"

This is a very important point of the entire Pipeline system.

For example:

```text
Import Data ✓
    ↓
Generate FID ✓
    ↓
Process Data ✓
    ↓
Peak Picking ✓
```

If user reruns `Process Data`, then:

```text
Import Data ✓
    ↓
Generate FID ✓
    ↓
Process Data ✓
    ↓
Peak Picking !
```

Peak Picking must become:

```text
OUTDATED
```

The subsequent Assignment, Figure, etc. should also become OUTDATED.

Cannot continue to display "Complete".

---

# 17. Pipeline Step behaviour after clicking

After user clicks a step in Pipeline:

> The middle area switches to the dedicated interface for this function

For example, click "Data Processing":

```text
Data processing

Input
    spectrum.fid

Processing Script
    processing.com

Parameters

    Size       [1024]
    SW         [12.0]
    SF         [600]

[Edit script]
[Run]
[Stop]

Execution Log
----------------
...
```

Click "SMILE Reconstruction":

```text
SMILE Reconstruction

Input Spectrum
    spectrum.ft2

Parameters

    Iterations
    Threshold
    Regularization
    ...

[Run]
[Auto-optimise parameters]

Optimization Result
-------------------
...
```

Don't let all parameters pile up on the main interface at the same time.

---

# 18. Pipeline should not be strongly coupled with GUI

GUI is only responsible for:

- Show Task
- Show task status
- Collect parameters
- Call Task
- Show execution results

The actual processing logic should be left in a separate backend / processing module.

Ideal structure:

```text
GUI
 ↓
Pipeline Controller
 ↓
Task
 ↓
Processing Backend
 ↓
Output
```

Instead of:

```text
GUI Button
 ↓
runs a large amount of processing code directly
```

This will make it easier to add new NMRPipe, SMILE, Peak Picking and other processing modules in the future.

---

# 19. Right: spectrum viewer

An existing spectrum viewer already exists, so:

> Do not reimplement the spectrum viewer

The existing Viewer should be embedded as an independent component in the right area.

Core linkage:

```text
Selecting a Spectrum on the left
        ↓
opens that Spectrum on the right automatically
        ↓
the middle pane shows the processing status of that Spectrum
```

For example:

```text
Data_001
├── Input
├── Processing
└── Output
    └── processed.ft2
```

User click:

```text
processed.ft2
```

Should:

1. Select file on the left.
2. The middle shows which Pipeline Step the result belongs to.
3. Automatic loading on the right processed.ft2.

---

# 20. Linkage between spectrum viewer and Pipeline

If the Pipeline is currently running:

```text
Process Data
```

After completion generate:

```text
processed.ft2
```

Should be able to provide:

```text
[View results]
```

Click to open directly on the right.

Similar:

```text
SMILE Reconstruction
      ↓
output.ft2
      ↓
[View spectrum]
```

---

# 21. Bottom Task/Log area

Add a collapsible bottom area.

Can be hidden by default.

Displayed when running the task:

```text
Task / Log

[RUNNING]
NMRPipe processing...

Command:
nmrPipe ...

stdout:
...

stderr:
...
```

On failure:

```text
[FAILED]

Error:
...

[View full log]
[Rerun]
```

In this way, the user can determine the cause of the processing failure without opening an additional terminal window.

---

# 22. Top menu bar

Use traditional software menus at the top, but keep it streamlined.

Suggestion:

```text
File
Project
Process
View
Tools
Window
Settings
Help
```

---

## 22.1 file

```text
New project
Open project
Open recent project
Save
Quit
```

## 22.2 items

```text
Project management
New experiment
New data
Project settings
Project structure
```

## 22.3 Processing

```text
Pipeline
Batch processing
Task queue
Processing history
```

## 22.4 View

```text
Spectrum viewer
Log
File browser
Show/hide panels
```

## 22.5 Tools

Add according to actual existing functions:

```text
Peak Picking
Spectrum Optimization
Data conversion
Script management
```

Don't add features that haven't been implemented yet.

## 22.6 window

```text
Restore default layout
Left panel
Pipeline panel
Spectrum panel
Log panel
```

## 22.7 settings

```text
Application settings
NMRPipe path
External programs
Default parameters
Compute resources
```

These settings must be determined based on the functionality actually supported by the existing project.

## 22.8 Help

```text
Documentation
Shortcuts
Log directory
About
```

---

# 23. Don’t add too many independent windows

Core principles:

> For functions that can be completed in the middle functional area, do not open additional windows

For example:

Not recommended:

```text
Main window
  ↓
click Data Processing
  ↓
a new window opens
  ↓
NMRPipe window
```

Recommend:

```text
Main window
  ↓
the middle area switches to Data Processing
```

Use independent windows only for advanced features that really require independent windows.

---

# 24. Quick operations

You can gradually increase:

```text
Double-click Data → open the Data Dashboard
Double-click a Spectrum → open it in the Viewer
Right-click → context menu
Enter → open the current object
Delete → delete
Ctrl+Z → undo, if the current architecture supports it
Ctrl+S → save the project state
```

Shortcut keys are not the first priority, and the core architecture cannot be affected by shortcut keys.

---

# 25. Data and Pipeline’s core data model

It is recommended that at least the following concepts exist internally:

```text
Project
Experiment
Dataset
Pipeline
PipelineStep
Artifact
Task
```

Where Artifact represents a file generated or used by the software.

For example:

```text
Dataset
    |
    +-- raw data
    |
    +-- FID
    |
    +-- processed spectrum
    |
    +-- reconstructed spectrum
    |
    +-- peak list
    |
    +-- assignment
    |
    +-- figure
```

PipelineStep then defines:

```text
Input artefact
      ↓
processing logic
      ↓
Output artefact
```

---

# 26. Basic definition of Pipeline Step

Each Step should be able to describe:

```text
id
name
description

inputs
outputs

dependencies

parameters

status

run()
validate()
get_output()
```

The specific implementation must be combined with the existing project architecture and does not require mechanical copying of this interface.

---

# 27. The existence of file cannot be used as the only basis for status

Don't just use:

```text
os.path.exists(output)
```

Determine whether the task is completed.

Because:

- File may exist but is of an older version
- The input file may have changed
- Parameter may have changed
- Handling script may have changed
- Pipeline logic may have changed

A more reliable approach is to save necessary task metadata, for example:

```text
input fingerprint
parameter hash
script/version information
output information
timestamp
status
```

If an existing project already has a similar mechanism, reuse it first.

If the first version cannot fully implement fingerprint for the time being, it should at least establish a clear state management interface to leave room for future expansion.

---

# 28. Error handling

Don't just play one when handling failure:

```text
Processing failed.
```

The user should be told:

```text
Processing failed

Step:
NMRPipe Processing

Reason:
xxx

Input:
xxx

Output:
xxx

[View log]
[Rerun]
```

If there is a clear error in stderr, the critical part should be shown where possible.

---

# 29. Batch processing

When the user selects Experiment, they can provide:

```text
[Batch processing]
```

For example:

```text
Experiment_01

Data_001 ✓
Data_002 ✓
Data_003 ▶
Data_004 🔒
```

Batch processing must respect each Data's own Pipeline dependency.

Don't reimplement a set of processing logic just for batch processing.

---

# 30. The current context must always be clear

There should be a clear current context in the interface, for example:

```text
Project_A / Experiment_01 / Data_001
```

Can be placed on the top toolbar or near the middle area title.

This way the user won't get lost in the current object when working with multiple experiments.

---

# 31. GUI status and file system status must be synchronized

If user:

- Delete file
- Modify file from external program
- New file
- Modify Pipeline output

Software needs to be refreshed/Rescan mechanism.

Provide at least:

```text
Refresh project
```

Changes can be detected automatically when necessary.

But don't scan the entire project unconditionally frequently and cause performance problems.

---

# 32. Important UX principles

The entire software should follow the following principles:

### Principle 1: Users should not guess what to do next

Pipeline should explicitly tell the user:

```text
Next step: data processing
```

### Principle 2: Functions that cannot be performed should be explained why

Don’t just:

```text
🔒
```

It is best to provide tooltip:

```text
complete Generate FID first
```

### Principle 3: Completed and expired must be distinguished

```text
SUCCESS ≠ OUTDATED
```

### Principle 4: File and function must be linked

After selecting file:

- The middle region knows which Pipeline it belongs to
- The Viewer on the right can open it

### Principle 5: Avoid duplicate windows

Core functions should be completed on the main interface as much as possible.

### Principle 6: Prioritize protection of original data

You cannot allow accidental deletion of original data just because of convenience.

---

# 33. Recommended final user workflow

User’s first time using the software:

```text
Open the application
 ↓
New Project
 ↓
Create Experiment
 ↓
Create/import Data
 ↓
Import raw NMR data
 ↓
Pipeline detects the status automatically
 ↓
Click Generate FID
 ↓
Click Data Processing
 ↓
View the spectrum
 ↓
SMILE Reconstruction
 ↓
Peak Picking
 ↓
Assignment
 ↓
Figure Generation
```

User does not need to remember:

- Which script runs first
- Which file is placed where?
- Which output is generated by which step
- Which step depends on which step

These should be managed by software.

---

# 34. Reconstruction requirements for existing projects

Before you start modifying the code:

## The first stage: only analysis, no modification

Read the scripts and modules in the existing project in sequence and create:

1. File list.
2. The function of each script.
3. Dependencies of each module.
4. Core call chain.
5. Data flow.
6. GUI structure.
7. Pipeline structure.
8. Spectrum viewer structure.
9. File generation logic.
10. Existing state management mechanism.

Output project architecture report.

---

## Stage 2: Establish logical relationships

Established based on the results of the first stage analysis:

```text
GUI
 ↓
Controller
 ↓
Pipeline
 ↓
Processing Modules
 ↓
File System / Artifacts
```

Also confirm:

- Which modules are duplicated
- Which modules have conflicting responsibilities
- Where do circular dependencies exist?
- Where are the states out of sync?
- Where to operate directly GUI
- Where to directly operate the file system
- Which Pipeline Steps have unclear dependencies

---

## The third stage: only raise questions, do not modify immediately

List:

```text
Issue id
Issue location
current behaviour
cause of the conflict
potential impact
suggested resolution
files involved
```

Don’t do a massive rewrite without confirming the architecture.

---

## Stage 4: Develop a reconstruction plan

In order of priority:

### P0
Errors that cause a program to not work correctly.

### P1
Problems that lead to incorrect data processing results or incorrect status.

### P2
GUI is coupled with the back-end logic and difficult to maintain.

### P3
UX, performance and code quality optimisation.

Solve P0/P1 first, do not do visual optimisation first.

---

# 35. Working mode for a large project

The desktop application and the backend are developed separately and integrated afterwards; when facing a large project, don't assume that you understand the entire project at once.

Suggestion:

```text
File-by-file analysis
    ↓
record each file's purpose
    ↓
summarise module relationships
    ↓
summarise the data flow
    ↓
analyse the Pipeline
    ↓
check for logical conflicts
    ↓
locate the exact code
    ↓
plan the change
    ↓
make a small change
    ↓
test
    ↓
then move on to the next part
```

After each stage is completed, the analysis results should be saved instead of relying entirely on the conversation context.

Recommended maintenance in the project:

```text
PROJECT_ARCHITECTURE.md
PROJECT_DATA_FLOW.md
PROJECT_PIPELINE.md
PROJECT_ISSUES.md
```

If project documentation already exists, priority should be given to updating the existing documentation rather than creating a large number of duplicate documents.

---

# 36. The most important development constraints

The following constraints apply to every change:

1. Don't reimplement existing functionality just to implement new GUI.
2. Do not delete existing spectrum viewers.
3. Don't put processing logic directly into GUI.
4. Don't write the Pipeline inside the GUI Button.
5. Don't do large-scale reconstruction without analysis.
6. Do not change existing data formats unless explicitly necessary.
7. Don't break existing script interfaces.
8. Do not overwrite original data by default.
9. Do not use whether the file exists as the only task completion judgment.
10. After modification, you must check whether the original functions are still available.
11. New functions should reuse existing backends as much as possible.
12. If conflicts are found in the existing architecture, report them first and then modify them.
13. If you cannot determine the purpose of a module, do not guess and continue to trace the calling relationship.
14. GUI, Pipeline, Processing Backend, File/Artifact Management should try to maintain the separation of responsibilities.

---

# 37. Priority of first version GUI

The first version does not require all advanced features to be implemented at once.

Prioritize implementation:

```text
P0
├── Three-pane main layout
├── Project / Experiment / Data project tree
├── Folder and data selection
├── Current context
├── Pipeline display
├── Pipeline status
├── Prerequisite checks
├── Embedding the existing spectrum viewer
└── Task / Log

P1
├── Data Dashboard
├── Experiment Dashboard
├── Project Dashboard
├── Context menus
├── OUTDATED status
└── Batch processing

P2
├── Pipeline parameter management
├── Pipeline history
├── A more complete task queue
├── Shortcuts
└── Advanced settings
```

Don't complicate the first version GUI in pursuit of completeness.

---

# 38. Ultimate Goal

The final software should make users feel:

> "I just need to find my data and the software tells me what to do next. "

Instead of:

> "I need to know which script, which directory, which parameter, and which output file should be processed first. "

Therefore:

**The project tree is responsible for "where am I". **.

**Pipeline is responsible for "what can i do/what to do next". **.

**The ribbon is responsible for "how to do it". **.

** The spectrum viewer is responsible for "what the result is". **.

**Task/Log is responsible for "what happened/why failed". **.

This is the core logic of the entire GUI architecture.

---

# 39. Checklist before starting a modification

Before actually modifying the code, please complete the following tasks:

1. Read the entire existing project.
2. Document the function of each script by file.
3. Establish module dependencies.
4. Create a data flow.
5. Find existing GUI structures.
6. Find existing Pipeline/Processing flow.
7. Find existing spectrum viewers.
8. Find out the file generation and status judgment logic.
9. Check your existing architecture against this document.
10. List conflicts and issues.
11. Give the reconstruction plan.
12. **Don’t modify the code on a large scale yet. **.

Only after the above analysis is completed, the phased implementation of GUI reconstruction begins.

Each modification should keep the project runnable and conduct corresponding testing as much as possible.

