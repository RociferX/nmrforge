import nmrglue as ng

for p in [
    "/home/<lab-user>/Desktop/sampleK.nmrpipe/102_direct_preview.ft3",
    "/home/<lab-user>/Desktop/sampleK.nmrpipe/102_joint.ft3",
]:
    try:
        dic, data = ng.pipe.read(p)
    except Exception as exc:
        print(p, "READ ERROR:", exc)
        continue
    print(p)
    print("  FDF1LABEL:", dic.get("FDF1LABEL"), "FDF2LABEL:",
          dic.get("FDF2LABEL"), "FDF3LABEL:", dic.get("FDF3LABEL"))
    print("  FDDIMORDER:", dic.get("FDDIMORDER"), "shape:", data.shape,
          "complex:", np.iscomplexobj(data) if "np" in dir() else "?")
