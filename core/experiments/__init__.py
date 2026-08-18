"""实验模板与先验：每个实验类型提供 priors / constraints / expected behavior。

导入本包即注册全部内置模板（模块内 register(TEMPLATE)）。
"""

from core.experiments import (  # noqa: F401
    cbcaconh,
    cch,
    nnh,
    cbcanh,
    cosy,
    generic,
    hmbc,
    hmqc,
    hnca,
    hncacb,
    hnco,
    hnco_ca,
    hnha,
    hsqc,
    noesy,
    roesy,
    tocsy,
)
