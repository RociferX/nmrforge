# Phase optimisation two-path (Simple/Advanced) design record

> Status: implementation and independent testing were completed; these routes were never wired
> into the existing backend/stepwise production orchestration.
> **Later note (2026-09-23)**: the "simple / advanced" display-layer phase route was superseded by
> the unified phase scheme in 0.2.106 - the display-layer phase engine, the NUS hybrid-flow
> optimiser and their accompanying tests have all been deleted from the tree; the current
> implementation is `workflow/phase_routes.py` plus the unified phase scheme. This page is kept as
> the design record of that time: the modules, entry points and tests described below are **not
> shipped with the current version**.
> Date: 2026-08-16

## 1. Goal

Unify NMR phase optimisation into two optional paths, both of which try to fit the working method of manual nmrDraw:

- Manual method: First process it normally, open spectrum, look at the one-dimensional spectrum dimension by dimension, and adjust phase in the display layer to make the peaks appear
  Absorption shape, record each dimension (p0, p1); at the same time, judge with the naked eye whether the baseline is flat and whether the peak is round, and decide.
  POLY and zero filling; fill these parameters back into the script and regenerate it to get a good spectrum.
- Automatic method: directly imitate the above sequence. The key fact is that the spectrum displayed by nmrDraw is a real spectrum, one-dimensional
  The adjustable phase of the spectrum is because it performs Hilbert transformation on the real spectrum to reconstruct the imaginary part, and then takes the real part for observation after rotation.
  Therefore, the automatic process also performs dimension-by-dimensional Hilbert display layer phase modulation on the real-type spectrum, rather than re-creating the replica.
  Intermediate spectrum.

## 2. Two ways

### 2.1 Simple approach (new method, all spectral types are unified)

Idea: First run it once to get the score, then evaluate it on the display layer, and then run it again.

1. The first pass: uniform tune `backend.process`,NUS tune `backend.reconstruct_nus`.
   Turn off the direct dimension search/display layer search and get the real final spectrum.
2. Display layer evaluation: read final spectrum, for each dimension:
   - Hilbert transform reconstructs the imaginary part of this dimension;
   - Select signal peak;
   - Cross-peak phase concentration fitting p1;
   - Anchored peak circle mean fit p0;
   - Verify with symmetry + positive peak constraint score;
   - Both baseline (both ends/central mean) and peak roundness (the half-maximum width of the strongest peak) are evaluated
3. Second pass: fill in each dimension (p0, p1) back to script:
   - Uniform: all dimensions are written `process` to direct_phase_override;
   - NUS: direct dimension writes back `reconstruct_nus` of direct_phase_override, indirect dimension writes back
     Phases of `finalize_nus`.
4. Get good results. Number of backend runs: uniform 2 times; NUS 3 times (SMILE twice + finalize.
   Once).

Features: low cost, repeatable, close to skilled labor; upper quality limit subject to Hilbert reconstruction and symmetry score.
The impact of the agreed offset from the real backend PS requires real data calibration.

### 2.2 Advanced Path (old method + NUS hybrid)

Idea: Uniform still uses the old method of full dimension re-run backend optimisation; NUS because direct dimension re-run SMILE.
The cost is high, direct dimension takes a simple approach to the display layer, and indirect dimension takes candidate back-end optimisation.

- Uniform: delegate existing `workflow.phase_optimize.optimize_phase_sequential`
  All dimensions are run candidate by candidate for backend scoring (dozens of backends).
- NUS Mixing process (`workflow.display_hybrid_optimize.optimize_nus_hybrid`):
  1. The first pass SMILE(direct dimension PS(0,0)) obtains the real final spectrum;
  2. Final spectrum direct dimension does Hilbert display layer phase modulation and records (p0, p1);
  3. Rerun SMILE with the recorded phase to obtain the replica plane with the correct direct dimension phase;
  4. Taking these complex planes as the starting point, run finalize and score each indirect dimension candidate by candidate, and select the best one;
  5. Use all the optimal phases to run the last finalize to generate a good spectrum.

Features: uniform retains the systematic search of "real spectrum + objective score"; NUS cannot be used in direct dimension.
Under the constraint of dozens of reruns SMILE, the display layer estimation is used as the direct dimension phase source, and the indirect dimension is still used.
Real backend candidate optimisation.

## 3. Detailed implementation

### 3.1 Display layer engine

File:`core/optimisation/display_phase_engine.py`.

Data object:

- `AxisPhaseEstimate(axis, p0, p1, score, windows)`
- `BaselineEstimate(axis, mode, order, score)`
- `FillEstimate(axis, fwhm_points, suggested, ratio)`

Core functions:

- `analytic_axis(real, axis)`: Perform Hilbert transform along the specified axis and return the analytical signal
- `search_axis_phase(real, axis)`:
  - `_row_peaks`: A 1D spectrum takes top-K local peaks (noise/Threshold filtering);
  - `_p1_concentration`: candidate p1 has the largest unit vector circular mean modulus of each peak after removing the slope
    Coarse grid + two levels of refinement, get signal slope p1;
  - P0: Anchor with the strongest trace, remove the peak phase after the slope of p1 and take the negative phase weighted circular mean, +/-180 is used
    Symmetry score positive peak disambiguation;
  - `_anchor_score`: Rotate the display layer in the peak window of the anchored trace, take the real part, and use
    `_symmetry_score` Rating;
  - Local +/-10°/+/-5° refinement, return to zero when the difference between p1 and zero phase is less than 1;
  - Gate:`|p1| > 20°` Return to zero (unimodal window is naturally insensitive to p1, preventing noise slopes)
- `assess_baseline(real, axis)`: Take the average of 8% at both ends and 8% in the middle, normalise
  Slope/curvature/offset;curvature > 0.05 determines order 2, slope > 0.05 determines.
  Order 1, otherwise auto(turn off baseline).
- `assess_fill(real, axis)`: The strongest peak is obtained by max projection in other dimensions, and the half-maximum width is calculated FWHM;
  FWHM < Target points (default 4) Zero filling is recommended.
- `inspect_spectrum(real, axes=None)`: One-time return dimension by dimension
  `{"phases", "baselines", "fills"}`.

### 3.2 NUS Hybrid arrangement

File:`workflow/display_hybrid_optimize.py`.

- `direct_axis_from_header(header, nucleus)`: match by FDF1/FDF2/FDF3 tag
  Direct dimension nuclide, get spectrum array axis index.
- `estimate_direct_phase(spectrum_path, experiment)`: Read final spectrum, in direct dimension
  Call `search_axis_phase`, return (p0, p1, score).
- `optimize_nus_hybrid(experiment, backend, *, p0_values, p1_values,
  score_fn, work_dir, base_params)`:
  1. `backend.reconstruct_nus`(direct/display search close);
  2. `estimate_direct_phase`;
  3. `backend.reconstruct_nus`(direct_phase_override is the estimated phase);
  4. Score candidate `backend.finalize_nus` + `score_fn` for each indirect dimension;
  5. Finally `backend.finalize_nus` once.
  Return `phases`, `backend_runs`, `candidates_scored`, `spectrum_path`.
  `direct_phase`, `logs`.

### 3.3 Path assignment

File:`workflow/phase_routes.py`.

- `axis_to_logical(experiment, axis)`: Spectral array axis index maps to F1/F2/F3 logical axis
- `estimate_all_axes(spectrum_path, experiment)`: Read real spectrum, display layer estimation dimension by dimension
  Return `{logical axis: (p0, p1)}`.
- `simple_route(experiment, backend,...)`: Two-pass process for all spectrum types (see 2.1)
- `advanced_route(experiment, backend, ...)`:
  - NUS → `optimize_nus_hybrid`;
  - uniform → `workflow.phase_optimize.optimize_phase_sequential`.

## 3.4 Display layer phase application principle (0.2.102)

- Reconstruct the imaginary part of the display layer, rotate the phase, take the real part, and directly call the nmrPipe function first
  (HT / PS -ht), do not use numpy/scipy to simulate it yourself; only if you can strictly prove that numpy and.
  NmrPipe can be replaced only if it is equivalent bit by bit, otherwise the nmrPipe function shall prevail.
- HT symbol/The axis must be generated against this dimension transformation in FT/TP/EXT script for each dimension, dimension by dimension
  Transpose to the pipe axis and then HT; mirror Hilbert (-ps90-180) and select according to the frequency direction of this dimension.
- The candidate display spectrum is generated using nmrPipe PS -p0 -p1 -ht -di, and then the advanced version fixed trace is reused
  Net absorption score to avoid residuals introduced by numpy simulated rotations.

- Differences in peak position locking: the advanced version is fixedly locked on the basic spectrum (the peak position of the real back-end candidate spectrum is stable); the simple version
  Dynamically locked on the PS -ht candidate display spectrum, because the reconstructed spectrum peak position of HT changes with phase and is fixed.
  Locking scores distortions. This is currently the only implementation difference other than imaginary sources, and is explicitly documented.

## 4. Key agreements and gates

- The display layer phase is the "correction phase": multiplying the analytical signal
  The peak after `exp(i·(p0 + p1·k/(n-1)))` is in the form of absorption; when script backfills p0/p1 as PS.
  Correction value.
- The Hilbert reconstruction is an estimate of the imaginary part of the real spectrum, not the true imaginary part of the original FID; unlike nmrPipe PS
  The real frequency domain rotation convention may have a system offset, and a display layer needs to be made on sampleI and sampleB.
  Phase ↔ PS phase calibration.
- P1 has weak discrimination in the single-peak window, and the current gate |p1| > 20° is reset to zero; if p1 is retained, it must be returned
  "Is p1 trustworthy?" rather than blindly writing script.
- Baseline / zero filling evaluation is currently available independently in the engine and has not yet been written in simple_route
  Baseline/zero_fill parameter (next step).

## 5. Test

- The tests that accompanied the display-layer phase engine and the NUS hybrid-flow optimiser
  (Hilbert dimension-wise phase recovery, near-zero phase, baseline order, zero-filling advice for
  narrow peaks; hybrid call order, direct-dimension override, indirect-dimension candidate
  selection, backend call counts): retired with the mechanism and no longer in the tree.
- `tests/test_phase_routes.py`:simple/advanced dispatch, uniform twice
  NUS direct dimension override + indirect dimension finalize, advanced uniform delegates to the old optimizer.

## 6. To-do (connect to production)

- Connect simple_route / advanced_route to stepwise or AutoProcessor
  Generate spectrum step;
- The real data calibration display layer phase is agreed with PS phase;
- Map the result of assess_baseline / assess_fill to baseline / zero_fill
  Parameter and write it back to regenerate;
- NUS The mixed path confirms the reuse of the reconstruction plane to avoid triggering SMILE other than the second reconstruct


## 7. Final unified plan (2026-08-17,replacement simple/Advanced allocation)

Conclusion: no longer simple/Two ways to advance, unified into one approach; the imaginary part of the display layer must be.
FID The real imaginary part after replicating FT is no longer reconstructed with HT.

### Process (implemented, 2026-08-17)

1. The first pass (replica preview): the entire production pipeline, only PS of the "search axis" does not add -di (the output of this axis is true.
   Imaginary part), other axes add -di according to the fixed phase; zero filling (same parameters as the old phase candidate); output production layout.
   Duplicate file (pipe2xyz -x, not axis-wise -y/-z -- to avoid transpose ambiguity):
   - Uniform: one preview pipeline for each axis (two for 2D F1/F2, three for 3D F3/F2/F1), the last axis
     Preview carries the fixed phase that has been searched for the previous axis (the old algorithm has fixed semantics axis by axis);
   - NUS: direct dimension reuse SMILE recon complex plane (axis 0); indirect dimension by finalize complex
     Provided by preview (this axis does not add -di, FT/-alt/ZTP, the agreement is guaranteed by the real backend).
2. Display layer phase modulation: read the duplicate file, rotate the memory frequency domain to get the real part, fix the trace and search the net absorption score dimension by dimension.
   (p0,p1) -- Coarse mesh p0 30° -> 1/3 Refine to 5° -> Platform circle median -> +/-90° symmetry.
   Disambiguation -> p1 {0,+/-22.5}; flat gate /Reproducibility/joint review consistent with old algorithm; zero extra.
   SMILE. NUS direct dimension uses the old 0.2.96 symmetry search (|p1|>20° to zero, score<30 to keep.
   (0,0)).
3. Finally, complete re-run: window function, zero filling, baseline, each dimension PS (fill in phase), EXT, -di, generated once.
   Good spectrum (uniform process; NUS finalize, direct dimension phase first rotate recon plane copy).

### Key principles (implementation corrections)

- Backend times: uniform 2D = 3(2 preview + 1 final run), 3D = 4;NUS 2D = 3(SMILE +
  1 preview + 1 finalize), 3D = 4(SMILE + 2 preview + 1 finalize). All are cheap.
  NmrPipe/finalize, no additional SMILE.
- Not used nmrPipe HT / scipy hilbert(deleted phase_ht_candidate_axis/
  phase_ht_candidate/hilbert_spectrum/display_phase_engine/
  display_hybrid_optimize).
- Delete the simple/advanced dispatch; `params["phase_route"]="none"` retain the old path escape
- SMILE Preliminary experiment conclusion: Do not accept complex direct dimension input (error report Imaginary in the direct
  Dim must be deleted) -> NUS stage1's -di remain.
- The 3D output axis sequence measured is (F2,F1,F3)(FDF header label is unreliable); complex preview is unpacked by search axis
  (Staggered real axes are not fixed: F2 preview on axis 0, F1 preview on axis 1).
- Experiment type symbol early constraints (0.2.106): presets/*.yaml added peak_sign(uniform/mixed;
  HNCACB=mixed). mixed score = |net absorption of each window| median + positive and negative coexistence constraints (one missing.
  Symbol x 0.7); uniform maintains signature net absorption median (positive peak preference resolution +/-180).
- +/-180° absolute sign disambiguation (0.2.106):presets/*.yaml new peak_sign_regions
  (Chemical shift partition + expected sign, HNCACB 13C axis Cα 40-70ppm burden/Cβ 15-45ppm positive).
  When there is a partition prior for the core where the search axis of the mixed experiment is located, the dominant sign of the strong peak in the statistical area: the two areas are opposite.
  And each is clean (>= 70%/ >= 4 strong peaks) and the absolute agreement is inconsistent with the default before p0+=180 is flipped (conservative).
  Absolute sign versus pulse sequence/Handling may flip, the default can be negated as a whole. sampleB F1≈0 is the same as the convention.
  Consistent, not flipped.
- Discrete peak trace selection (only mixed experiments): threshold 95th percentile + half-height window ratio (duty) <= 0.5 +
  Peak significance >= 2.5, filter the central mixed peak cluster; uniform keeps the old 99.5 quantile and all strong trace locks.
  (Discrete filtering once biased sampleL by 180°, and the range has been limited).
- POLY order: preview skips POLY on the search axis (the old scheme is rotated candidate by candidate and then POLY; solidified
  (0,0) POLY will contaminate the score after memory rotation, VM sampleF F1 calibration). 3D finalize would have been.
  There is no POLY.

### State

- Already implemented: dimension-by-dimensional complex preview (uniform/NUS), memory phase modulation (old algorithm judgment standard), symbolic early constraints
  Discrete peak selection, 3D axis order repair, delete HT and simple/Advanced allocation, generate_spectrum unify.
  Default process (phase_route=none reserved).
- VM Full spectrum same decision regression (sampleI/103/3/4/5, sampleA 25%/100%, sampleB):
  P0 is consistent with old optimize_phase_sequential (+/-2.5–10°, mostly <= 5°);
  SampleL old simple path F2=0°/F1=300° exception elimination (unified solution F2=307.5°/F1=87.5°);
  SampleB(HNCACB)F2=90°/F1≈0° is consistent with manual (old sequential is gate fallback (0,0));
  Nus20_100/nus20_25 direct dimension and F1 are both (0,0)/(5,0).
- To-do: baseline / zero filling evaluation result writeback (assess_baseline/assess_fill used to belong to the display engine
  Deleted with the HT path, the baseline / zero filling of the unified process is explicitly controlled by params); 3D uniform.
  Measured (no ready-made 3D uniform dataset); +/-180° sign ambiguity still exists in mixed experiments.
  (A priori peak attribution is required, which is beyond the phase search range).
