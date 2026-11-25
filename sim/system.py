"""Single source of truth for the FWS Loop 1 co-simulation system.

Each component instance has:
  - the FMU type it runs (one FMU per *type*, e.g. both pumps use FWS_StratosGIGA2Pump.fmu)
  - the USD prim it is bound to (prim paths can change; the binding in usd/simulation.usda is what counts)
  - its aif_location (stable functional location, used as SSP component name)
  - parameters (inline, or an .ssv file keyed by the serial / wilo_asset)
Connections are (from_instance.output -> to_instance.input). Sums are handled by the master.
"""

COMPONENTS = {
    # name           fmu type             prim path                                       aif_location              params
    "GB300_Pod1":  ("GB300Load",        "/AIF/Hall1/WhiteSpace/Pod1/Racks",            "AIF-01/WS/POD1/RACKS",    {"nRacks": 4, "PRack": 116e3}),
    "TCS_Pod1":    ("TCSLoop",          "/AIF/Hall1/WhiteSpace/Pod1/TCS",              "AIF-01/WS/POD1/TCS",      {"C": 1.7e6}),
    "CDU_01":      ("CDU",              "/AIF/Hall1/WhiteSpace/Pod1/CDU_01",           "AIF-01/WS/POD1/CDU-01",   {"eps": 0.8, "T_set": 42}),
    "FWS_Supply":  ("Pipe",             "/AIF/Hall1/FWS/Loop1/Piping/FWS_Supply",      "AIF-01/FWS/L1/PIPE-S",    {"L": 48.0, "sumK": 8.0}),
    "FWS_Return":  ("Pipe",             "/AIF/Hall1/FWS/Loop1/Piping/FWS_Return",      "AIF-01/FWS/L1/PIPE-R",    {"L": 46.0, "sumK": 6.0}),
    "P_101":       ("StratosGIGA2Pump", "/AIF/Hall1/FWS/Loop1/P_101",                  "AIF-01/FWS/L1/P-101",     "params/SN-A1001.ssv"),
    "P_102":       ("StratosGIGA2Pump", "/AIF/Hall1/FWS/Loop1/P_102",                  "AIF-01/FWS/L1/P-102",     "params/SN-A1002.ssv"),
    "Hydraulics":  ("LoopHydraulics",   "/AIF/Hall1/FWS/Loop1",                        "AIF-01/FWS/L1",           {}),
    "Chiller_01":  ("Chiller",          "/AIF/Plant/Chiller_01",                       "AIF-01/CHW/CH-01",        {"T_set": 30}),
    "BMS":         ("BMSController",    "/AIF/Hall1/FWS/Loop1/BMS",                    "AIF-01/BMS/FWS-L1",       {"delay": 15}),
}

CONNECTIONS = [
    ("GB300_Pod1.Q_it", "TCS_Pod1.Q_it"),
    ("CDU_01.Q_hx", "TCS_Pod1.Q_hx"),
    ("TCS_Pod1.T_ret", "CDU_01.T_tcs_ret"),
    ("Hydraulics.V", "CDU_01.V_flow"),
    ("Chiller_01.T_sup", "CDU_01.T_fws_in"),
    ("Hydraulics.V", "FWS_Supply.V_flow"),
    ("Hydraulics.V", "FWS_Return.V_flow"),
    ("Hydraulics.V", "Chiller_01.V_flow"),
    ("CDU_01.T_fws_out", "Chiller_01.T_ret"),
    ("Hydraulics.V1", "P_101.V_flow"),
    ("Hydraulics.V2", "P_102.V_flow"),
    ("P_101.dp0", "Hydraulics.dp0_1"),
    ("P_101.k_p", "Hydraulics.k_p1"),
    ("P_102.dp0", "Hydraulics.dp0_2"),
    ("P_102.k_p", "Hydraulics.k_p2"),
    ("BMS.run1", "P_101.run"),
    ("BMS.run2", "P_102.run"),
    # sum node: Hydraulics.R = FWS_Supply.k + FWS_Return.k + CDU_01.k + Chiller_01.k (series resistances)
]
RESISTANCE_SUM = ["FWS_Supply.k", "FWS_Return.k", "CDU_01.k", "Chiller_01.k"]
EXTERNAL_INPUTS = ["GB300_Pod1.u", "P_101.fault", "P_102.fault", "BMS.fault1", "BMS.fault2"]
