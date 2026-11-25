package FWS "Facility water system, loop 1: two Wilo-Stratos GIGA2.0-I pumps (duty P-101 / standby P-102) feeding a CDU that cools 4 x GB300 NVL72.
Every component is causal (signal inputs/outputs only), so each one is exported as its own FMI 2.0 co-simulation FMU.
Pump curves and plant data are ILLUSTRATIVE placeholders, not Wilo or Vertiv data."

  model GB300Load "IT heat load of n GB300 NVL72 racks (liquid-cooled share)"
    parameter Integer nRacks = 4 "Number of racks on this CDU";
    parameter Real PRack(unit="W") = 116e3 "Liquid heat per rack at 100 % load";
    parameter Real tau(unit="s") = 2 "Power response time constant";
    parameter Real u0 = 0.4 "Initial load fraction";
    Modelica.Blocks.Interfaces.RealInput u(start=0.4) "Load fraction 0..1 (workload schedule)";
    Modelica.Blocks.Interfaces.RealOutput Q_it(unit="W") "Heat into the TCS";
    Real P(start=0.4*4*116e3, fixed=false, unit="W");
  initial equation
    P = nRacks*PRack*u0;
  equation
    der(P) = (nRacks*PRack*max(0, min(1, u)) - P)/tau;
    Q_it = P;
  end GB300Load;

  model TCSLoop "Technology cooling system: lumped thermal mass of coolant, cold plates and manifolds"
    parameter Real C(unit="J/K") = 1.7e6 "Thermal capacity (approx. 400 l water + metal)";
    parameter Real T0(unit="degC") = 46.6 "Initial return temperature";
    Modelica.Blocks.Interfaces.RealInput Q_it(unit="W") "Heat from racks";
    Modelica.Blocks.Interfaces.RealInput Q_hx(unit="W") "Heat removed by the CDU heat exchanger";
    Modelica.Blocks.Interfaces.RealOutput T_ret(unit="degC") "TCS return temperature (hot side into CDU)";
    Real T(start=46.6, fixed=false, unit="degC");
  initial equation
    T = T0;
  equation
    der(T) = (Q_it - Q_hx)/C;
    T_ret = T;
  end TCSLoop;

  model CDU "Liquid-to-liquid CDU (e.g. Vertiv XDU2300): HX (epsilon-NTU) + FWS-side control valve, PI on TCS supply temperature"
    parameter Real eps = 0.8 "HX effectiveness";
    parameter Real C_tcs(unit="W/K") = 46.4e3 "TCS-side capacity rate (fixed secondary flow)";
    parameter Real rho_cp(unit="J/(m3.K)") = 4.18e6 "Volumetric heat capacity of FWS fluid";
    parameter Real T_set(unit="degC") = 42 "TCS supply setpoint";
    parameter Real k_hx = 0.0227 "HX loss coefficient, kPa/(m3/h)^2 (40 kPa at 42 m3/h)";
    parameter Real Kv(unit="m3/h") = 63 "Control valve Kv at full opening";
    parameter Real kP = 0.08 "PI gain (1/K)";
    parameter Real Ti(unit="s") = 30 "PI integral time";
    parameter Real yMin = 0.1;
    parameter Real y0 = 0.35 "Initial valve position";
    Modelica.Blocks.Interfaces.RealInput V_flow(unit="m3/h") "FWS flow through the CDU";
    Modelica.Blocks.Interfaces.RealInput T_fws_in(unit="degC") "FWS supply temperature";
    Modelica.Blocks.Interfaces.RealInput T_tcs_ret(unit="degC") "TCS return temperature";
    Modelica.Blocks.Interfaces.RealOutput Q_hx(unit="W");
    Modelica.Blocks.Interfaces.RealOutput T_tcs_sup(unit="degC") "TCS supply temperature to racks";
    Modelica.Blocks.Interfaces.RealOutput T_fws_out(unit="degC");
    Modelica.Blocks.Interfaces.RealOutput dp(unit="kPa") "FWS-side pressure loss HX + valve";
    Modelica.Blocks.Interfaces.RealOutput y "Valve position";
    Modelica.Blocks.Interfaces.RealOutput k "Hydraulic resistance dp = k*V^2, kPa/(m3/h)^2 (coupling variable)";
    Real I(start=0.35, fixed=false) "PI integrator state";
    Real C_fws(unit="W/K");
    Real Cmin(unit="W/K");
    Real yRaw;
    Real e;
    Real Vp;
  initial equation
    I = y0;
  equation
    Vp = max(V_flow, 0);
    C_fws = rho_cp*Vp/3600;
    Cmin = min(C_fws, C_tcs);
    Q_hx = eps*Cmin*max(T_tcs_ret - T_fws_in, 0);
    T_tcs_sup = T_tcs_ret - Q_hx/C_tcs;
    T_fws_out = T_fws_in + Q_hx/max(C_fws, 1);
    e = T_tcs_sup - T_set;
    yRaw = I + kP*e;
    der(I) = if (yRaw >= 1 and e > 0) or (yRaw <= yMin and e < 0) then 0 else kP*e/Ti;
    y = max(yMin, min(1, yRaw));
    k = k_hx + 100/(Kv*y)^2;
    dp = k*Vp^2;
  end CDU;

  model Pipe "Pipe run with fittings: Darcy-Weisbach, Swamee-Jain friction factor. One FMU type, parameterised per instance."
    parameter Real L(unit="m") = 40 "Developed length";
    parameter Real D(unit="m") = 0.0703 "Inner diameter (DN65 steel)";
    parameter Real rough(unit="m") = 4.5e-5 "Absolute roughness";
    parameter Real sumK = 6 "Sum of fitting loss coefficients";
    parameter Real rho(unit="kg/m3") = 997;
    parameter Real nu(unit="m2/s") = 0.8e-6 "Kinematic viscosity at ~30 degC";
    Modelica.Blocks.Interfaces.RealInput V_flow(unit="m3/h");
    Modelica.Blocks.Interfaces.RealOutput dp(unit="kPa") "Pressure loss";
    Modelica.Blocks.Interfaces.RealOutput v(unit="m/s") "Mean velocity";
    Modelica.Blocks.Interfaces.RealOutput k "Hydraulic resistance dp = k*V^2, kPa/(m3/h)^2 (coupling variable)";
    Real Re;
    Real f;
    Real vk "Velocity used for the resistance (floored to keep turbulent friction factor)";
  equation
    v = abs(V_flow)/3600/(Modelica.Constants.pi*D^2/4);
    vk = max(v, 0.3);
    Re = vk*D/nu;
    f = 0.25/(log10(rough/(3.7*D) + 5.74/Re^0.9))^2;
    k = (f*L/D + sumK)*rho/2/1000/(3600*Modelica.Constants.pi*D^2/4)^2;
    dp = k*V_flow^2;
  end Pipe;

  model StratosGIGA2Pump "Wilo-Stratos GIGA2.0-I 65/1-37/4,0 (art. 2204744) with built-in dp-v control.
  ILLUSTRATIVE curve H = (n/nMax)^2*H0 - a*Q^2. Replace with the Wilo-Select export; per-serial values come from an .ssv parameter set."
    parameter Real H0(unit="m") = 37 "Shut-off head at nMax (catalog H max)";
    parameter Real a = 0.006 "Curve coefficient, m/(m3/h)^2";
    parameter Real nMax(unit="1/min") = 4620;
    parameter Real P1Max(unit="W") = 4300;
    parameter Real etaPeak = 0.62 "Hydraulic efficiency peak";
    parameter Real Q_etaPeak(unit="m3/h") = 45;
    parameter Real etaDrive = 0.9 "IE5 motor x electronics";
    parameter Real H_design(unit="m") = 18 "dp-v design head";
    parameter Real Q_design(unit="m3/h") = 42 "dp-v design flow";
    parameter Real tRamp(unit="s") = 8 "Ramp time 0 -> nMax";
    parameter Real tauCoast(unit="s") = 1.0 "Coast-down time constant after trip";
    parameter Real kP = 0.08 "Speed controller gain (speed ratio per m)";
    parameter Real Ti(unit="s") = 1.5;
    parameter Real s0 = 0 "Initial speed ratio";
    parameter Real rho(unit="kg/m3") = 997;
    Modelica.Blocks.Interfaces.RealInput V_flow(unit="m3/h") "Flow through this pump";
    Modelica.Blocks.Interfaces.RealInput run "Run command from BMS (1 = run)";
    Modelica.Blocks.Interfaces.RealInput fault "Trip (1 = tripped)";
    Modelica.Blocks.Interfaces.RealOutput dp(unit="kPa") "Pressure rise";
    Modelica.Blocks.Interfaces.RealOutput H(unit="m") "Head";
    Modelica.Blocks.Interfaces.RealOutput n(unit="1/min") "Speed";
    Modelica.Blocks.Interfaces.RealOutput P1(unit="W") "Electrical input power";
    Modelica.Blocks.Interfaces.RealOutput H_set(unit="m") "dp-v setpoint";
    Modelica.Blocks.Interfaces.RealOutput dp0(unit="kPa") "Shut-off pressure at current speed (coupling variable)";
    Modelica.Blocks.Interfaces.RealOutput k_p "Curve coefficient dp = dp0 - k_p*Q^2, kPa/(m3/h)^2 (coupling variable)";
    Real s(start=0, fixed=false) "Speed ratio n/nMax";
    Real I(start=0, fixed=false) "Controller integrator";
    Real sCmd;
    Real eta;
    Real Qr "Flow scaled to nMax";
    Real Q;
    Boolean on;
  initial equation
    s = s0;
    I = s0;
  equation
    Q = max(V_flow, 0);
    on = run > 0.5 and fault < 0.5;
    H = s^2*H0 - a*Q^2;
    dp = rho*9.81*H/1000;
    dp0 = rho*9.81*s^2*H0/1000;
    k_p = rho*9.81*a/1000;
    H_set = H_design*(0.5 + 0.5*min(Q/Q_design, 1.2));
    sCmd = max(0, min(1, I + kP*(H_set - H)));
    der(I) = if not on then -I/tauCoast else (if (sCmd >= 1 and H_set > H) or (sCmd <= 0 and H_set < H) then 0 else kP*(H_set - H)/Ti);
    der(s) = if on then max(-1/tRamp, min(1/tRamp, (sCmd - s)/0.3)) else -s/tauCoast;
    n = s*nMax;
    Qr = Q/max(s, 0.05);
    eta = max(0.1, etaPeak*(1 - ((Qr - Q_etaPeak)/Q_etaPeak)^2));
    P1 = if s > 0.01 then min(P1Max, rho*9.81*max(H, 0)*Q/3600/(eta*etaDrive) + 30) else 0;
  end StratosGIGA2Pump;

  model LoopHydraulics "Fluid inertia of two parallel pump branches (each with a check valve) and the common loop.
  Turns the hydraulic network into ODEs, so the components can be coupled causally in co-simulation.
  Coupling uses slowly varying quantities (pump shut-off pressure and curve coefficient, loop resistance),
  not pressures: the stiff flow/pressure algebra stays inside this FMU, which keeps fixed-step co-simulation stable."
    parameter Real Lb = 1 "Branch inertance, kPa per (m3/h)/s";
    parameter Real Lc = 8 "Common loop inertance, kPa per (m3/h)/s";
    parameter Real k_cv = 0.004 "Check valve + branch fittings, kPa/(m3/h)^2";
    parameter Real V1_0(unit="m3/h") = 0;
    parameter Real V2_0(unit="m3/h") = 0;
    Modelica.Blocks.Interfaces.RealInput dp0_1(unit="kPa") "Shut-off pressure P-101 at current speed";
    Modelica.Blocks.Interfaces.RealInput k_p1 "Curve coefficient P-101";
    Modelica.Blocks.Interfaces.RealInput dp0_2(unit="kPa") "Shut-off pressure P-102 at current speed";
    Modelica.Blocks.Interfaces.RealInput k_p2 "Curve coefficient P-102";
    Modelica.Blocks.Interfaces.RealInput R "Sum of loop resistances (pipes + CDU + chiller), kPa/(m3/h)^2";
    Modelica.Blocks.Interfaces.RealOutput V1(unit="m3/h");
    Modelica.Blocks.Interfaces.RealOutput V2(unit="m3/h");
    Modelica.Blocks.Interfaces.RealOutput V(unit="m3/h") "Loop flow";
    Modelica.Blocks.Interfaces.RealOutput p_hdr(unit="kPa") "Header pressure rise";
    Modelica.Blocks.Interfaces.RealOutput dp_loss(unit="kPa") "Loop pressure loss";
    Real x1(start=0, fixed=false);
    Real x2(start=0, fixed=false);
    Real d1 "Branch 1 net pressure (pump minus branch losses)";
    Real d2;
    Real pBoth;
    Real pOnly1;
    Real pOnly2;
    Boolean c1 "Check valve 1 closed";
    Boolean c2 "Check valve 2 closed";
  initial equation
    x1 = V1_0;
    x2 = V2_0;
  equation
    V1 = max(x1, 0);
    V2 = max(x2, 0);
    V = V1 + V2;
    d1 = dp0_1 - (k_p1 + k_cv)*x1*abs(x1);
    d2 = dp0_2 - (k_p2 + k_cv)*x2*abs(x2);
    dp_loss = R*V^2;
    pBoth = (Lc*(d1 + d2) + Lb*dp_loss)/(2*Lc + Lb);
    pOnly1 = (Lc*d1 + Lb*dp_loss)/(Lc + Lb);
    pOnly2 = (Lc*d2 + Lb*dp_loss)/(Lc + Lb);
    c1 = x1 <= 0 and d1 < pOnly2;
    c2 = x2 <= 0 and d2 < pOnly1;
    p_hdr = if c1 and c2 then 0 elseif c1 then pOnly2 elseif c2 then pOnly1 else pBoth;
    der(x1) = if c1 then 0 else (d1 - p_hdr)/Lb;
    der(x2) = if c2 then 0 else (d2 - p_hdr)/Lb;
  end LoopHydraulics;

  model Chiller "Chiller evaporator as FWS boundary: supply temperature with first-order response, evaporator pressure loss"
    parameter Real T_set(unit="degC") = 30;
    parameter Real tau(unit="s") = 60;
    parameter Real k_evap = 0.017 "kPa/(m3/h)^2, ~30 kPa at 42 m3/h";
    parameter Real rho_cp(unit="J/(m3.K)") = 4.18e6;
    Modelica.Blocks.Interfaces.RealInput V_flow(unit="m3/h");
    Modelica.Blocks.Interfaces.RealInput T_ret(unit="degC") "FWS return from CDU";
    Modelica.Blocks.Interfaces.RealOutput T_sup(unit="degC");
    Modelica.Blocks.Interfaces.RealOutput dp(unit="kPa");
    Modelica.Blocks.Interfaces.RealOutput Q_ch(unit="W") "Heat taken by the chiller";
    Modelica.Blocks.Interfaces.RealOutput k "Hydraulic resistance, kPa/(m3/h)^2 (coupling variable)";
    Real T(start=30, fixed=false);
  initial equation
    T = T_set;
  equation
    der(T) = (T_set - T)/tau;
    T_sup = T;
    k = k_evap;
    dp = k_evap*max(V_flow, 0)^2;
    Q_ch = rho_cp*max(V_flow, 0)/3600*max(T_ret - T_sup, 0);
  end Chiller;

  model BMSController "Duty/standby logic: start the standby pump after a delay when the duty pump reports a fault"
    parameter Real delay(unit="s") = 15 "Standby start delay";
    Modelica.Blocks.Interfaces.RealInput fault1 "Fault from P-101";
    Modelica.Blocks.Interfaces.RealInput fault2 "Fault from P-102";
    Modelica.Blocks.Interfaces.RealOutput run1;
    Modelica.Blocks.Interfaces.RealOutput run2;
    Modelica.Blocks.Interfaces.RealOutput alarm "1 while redundancy is lost";
    discrete Real tFault(start=1e9, fixed=true);
  equation
    when fault1 > 0.5 then
      tFault = time;
    end when;
    run1 = if fault1 > 0.5 then 0 else 1;
    run2 = if fault2 < 0.5 and time >= tFault + delay then 1 else 0;
    alarm = if fault1 > 0.5 or fault2 > 0.5 then 1 else 0;
  end BMSController;

  annotation (uses(Modelica(version="4.1.0")));
end FWS;
