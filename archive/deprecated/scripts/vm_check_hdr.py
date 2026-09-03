import nmrglue as ng

for p in [
    "/home/<lab-user>/Desktop/sampleK.nmrpipe/102.ft3",
    "/home/<lab-user>/Desktop/data/sampleB.nmrpipe/28.ft3",
]:
    dic, data = ng.pipe.read(p)
    print(p)
    print("  FDF1LABEL:", dic.get("FDF1LABEL"), "FDF2LABEL:",
          dic.get("FDF2LABEL"), "FDF3LABEL:", dic.get("FDF3LABEL"))
    print("  FDDIMORDER:", dic.get("FDDIMORDER"), "shape:", data.shape)
    print("  FDSIZE:", dic.get("FDSIZE"), "FDF3SIZE:", dic.get("FDF3SIZE"))
