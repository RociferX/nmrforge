# Experiment template (shipped data: nmrforge_data/presets/)

Template = prior + constraints + desired behaviour (Framework §43), the specific parameters are determined by the optimizer.
YAML is the single data source for template (starting from 0.2.111):core/experiments/registry.from_yaml +.
Load_presets Implementation, import core.experiments that is, register all templates from nmrforge_data/presets/*.yaml.
(Double registration by display name and file stem); The original module-by-module Python template has been deleted and is no longer dual-source maintained.
The `peak_sign` of each template describes the peak symbol convention: `uniform` = the same number of signal peaks (HSQC/CBCA(CO)NH, etc.).
`mixed`=positive and negative peaks coexist (HNCACB, etc., 13Cα/13Cβ reverse phase). Phase optimisation is done as follows:
Mixed uses "|net absorption| median + positive and negative coexistence" to score, uniform uses signed net absorption.
`peak_sign_regions` gives a chemical shift partition sign prior (such as HNCACB for the 13C axis Cα/Cβ.
Ppm interval with expected sign), +/-180° absolute sign disambiguation for mixed experiments. Note the absolute sign.
Conventional pulse sequence/The processing method may be reversed, the default value of this preset comes from VM sampleB measured.
(Cα burden/Cβ is positive), if the dataset is opposite, it can be negated as a whole.
`priors` of each template gives the chemical shift range of each nucleus, which can be used to further confirm the nucleus according to the chemical shift when judging the experiment type.
(For example, the 13C of NCO should fall in the 165–185 ppm carbonyl region, distinguished from the 13Cα 40–70 ppm of NCA).
Chemical shift intervals are calculated as BMRB (Ulrich et al., Nucleic Acids Res. 36, D402 (2008)).
Compared with the commonly used ranges in solid-state NMR literature (1H -5–20, 15N 90–140 (homonuclear 15N-15N 90–160).
13C Fat 10–75, Cα 40–70, Cβ 15–45, Carbonyl 165–185, 13C Full Spectrum 10–190).

## 1D spectrum (direct detection of one dimension, no indirect dimension, no peak selection)

- Generic_1d.yaml: Safety cover for unknown 1D (Generic1D, not shown in the type drop-down);
- 1h_1d.yaml:1H one-dimensional spectrum (1H-1D,auto_phase);
- 13c_1d.yaml:13C one-dimensional spectrum (13C-1D);
- 31p_1d.yaml:31P one-dimensional spectrum (31P-1D);
- 19f_1d.yaml:19F one-dimensional spectrum (19F-1D);

1D data has no indirect dimension, generating spectrum direct connection process (no SMILE/replica preview / parameter optimisation).
By default, EXT is not performed (the entire spectrum is retained); the peak selection step is hidden for 1D.

## 2D spectrum

- hsqc.yaml: 15N-1H HSQC (1H 6–11, 15N 90–135)
- hsqc_13c.yaml: 13C-1H HSQC (1H 0.5–6.5, 13C 10–80)
- hmqc_15n.yaml: 15N-1H HMQC
- hmqc_13c.yaml: 13C-1H HMQC
- Hmbc_13c.yaml: 13C-1H HMBC (remote, 13C 10–200)
- hmbc_15n.yaml: 15N-1H HMBC
- Cosy.yaml / tocsy.yaml / noesy.yaml / roesy.yaml: 1H-1H same core (both axes are 1H, 0–10 ppm)

## 3D spectrum (protein triple resonance/homonuclear)

- hnca.yaml: HNCA (13Cα 40–70)
- hncacb.yaml: HNCACB (13Cα+β 10–80)
- cbcaconh.yaml: CBCA(CO)NH (13Cα+β 10–80)
- cbcanh.yaml: CBCANH (13Cα+β 10–80)
- Hnco.yaml: HNCO (13C carbonyl 165–185)
- hncoca.yaml: HN(CO)CA (13Cα 40–70)
- Hncaco.yaml: HN(CA)CO (13C carbonyl 165–185)
- Hnha.yaml: HNHA (third dimension 1Hα 0–11)
- hcaconh.yaml: H(CA)NH
- Hcconh.yaml: H(CCO)NH (13C fat 10–70)
- Ccconh.yaml: C(CCO)NH (13C-13C two axes)
- hbhaconh.yaml: HBHA(CO)NH (1Hα/β 0–11)
- hcch_tocsy.yaml: HCCH-TOCSY (1H-13C-1H)
- cch_tocsy.yaml: CCH-TOCSY (13C-13C-1H)
- Noesy_hsqc_15n.yaml: 3D NOESY-HSQC (15N editor, 1H-15N-1H)
- Noesy_hsqc_13c.yaml: 3D NOESY-HSQC (13C editor, 1H-13C-1H)

## Solid NMR (a type not found in liquid NMR, identified by nuclear combination + PULPROG)

The experiment type and chemical shift range are from the public literature: solid state nuclear magnetic experiment library ssNMRlib.
(Vallet et al., Magn. Reson. 1, 331 (2020));13C detection main chain identification kit.
(Wiegand et al., Biomol. NMR Assign. 10, 101 (2016));BMRB Chemical shift statistics.
(Ulrich et al., Nucleic Acids Res. 36, D402 (2008)). PULPROG Keywords are.
Literature/measured common naming (SPECIFIC-CP nca/nco, DARR/PDSD/RFDR/CORD/INADEQUATE/.
TEDOR/PAIN-CP, FSLGhetcor, etc.), the classifier is based on "core combination priority, same-core combination candidate.
PULPROG "Fine row" identification.

### 2D 15N-13C main chain/distance

- nca.yaml: NCA (15N-13Cα, SPECIFIC-CP, Baldus et al., Mol. Phys. 95, 1197 (1998))
- Nco.yaml: NCO (15N-13C' carbonyl)
- Tedor.yaml: TEDOR (15N-13C through-space distance, Hing et al., JMR 96, 205 (1992))
- Paincp.yaml: PAIN-CP (15N-13C proton-assisted CP, Lewandowski et al., JACS 129, 728 (2007))

### 2D 13C-13C side chain / long range

- Darr.yaml: DARR (Dipole-assisted rotational resonance, Takegoshi et al., Chem. Phys. Lett. 344, 631 (2001))
- Pdsd.yaml: PDSD (Proton-driven spin diffusion, Szeverenyi et al., JMR 47, 462 (1982))
- Rfdr.yaml: RFDR (RF-driven recoupling, Bennett et al., J. Chem. Phys. 96, 8624 (1992))
- Cord.yaml: CORD (Combined R2nv driver, Hou et al., J. Chem. Phys. 139, 064201 (2013))
- Inadequate.yaml: INADEQUATE (13C double quantum DQ, Bax et al., JACS 102, 4849 (1980))
- Hcc.yaml: HCC (data naming cshi.hCC_sd related to the 13C-13C spin diffusion/DARR class)

### 2D 1H-X related / 1H-1H space

- hetcor.yaml: HETCOR (1H-13C CP/FSLG, van Rossum et al., JMR 124, 516 (1997))
- hnhcor.yaml: HNHETCOR (1H-15N CP/FSLG)
- Chhc.yaml / nhhc.yaml: CHHC/NHHC (via the 1H-1H spatial correlation of 13C/15N
  Lange et al., JACS 124, 9704 (2002); kernel combination to be verified with real data).
- Nn.yaml: NN (2D 15N-15N proton-assisted recoupling, RNA base pairing, etc.)

### 3D 15N/13C/13C (13C direct detection)

- Ncacx.yaml: NCACX (15N-13Cα-13C side chain)
- Ncocx.yaml: NCOCX (15N-13C'-13C side chain)
- Ncacb.yaml: NCACB (15N-13Cα-13Cβ,Cα/Cβ reversed phase -> mixed)
- ncocacb.yaml: NCOCACB (15N-13C'-13Cα/β, mixed)

### 3D 13C/15N/13C sequence walking

- canco.yaml: CANCO (13Cα-15N-13C')
- Canco_ca.yaml: CAN(CO)CA (13Cα(i/i-1)-15N-13Cα,CO only transfers but does not collect)
- Cbcanco.yaml: CBCANCO (13Cα/β-15N-13C',Cα/Cβ inverted -> mixed)
- Ccc.yaml: CCC (3D 13C-13C-13C spin diffusion)

### 3D 1H detection

- Cch.yaml: CCH (13C-13C related detected by 1H)
- Nnh.yaml: NNH (related to 15N-15N detected by 1H)

Note: 1D type (CP13C/CP15N/PROTON1D/C13_1D) is currently unavailable YAML -- test_gui_presets.
Currently only ndim=2/3 is allowed, wait for GUI to release ndim=1 before adding it.

## Universal fallback

- Generic_2d.yaml / generic_3d.yaml: Conservative pipeline for unidentified experiments
