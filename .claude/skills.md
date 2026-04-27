# Agentic AI Skills Definition: Process Simulation & Reporting

This document outlines the specialized skills and tools for an Agentic AI designed to interface with Simio Simulation Software and automate high-fidelity engineering reports.

## 1. Core Simulation Intelligence
### Simio Object & Logic Interpretation
* **Structure Analysis:** Ability to parse `.spfx` metadata or model descriptions to understand entity flows, server capacities, and resource constraints.
* **Logic Auditing:** Identifying potential deadlocks or infinite loops in "Standard Property" configurations or "Process Logic" steps.
* **Bottleneck Heuristics:** Automatically ranking model objects by utilization percentage and identifying primary constraints in the system throughput.

### Stochastic Data Management
* **Distribution Fitting:** Using maximum likelihood estimation (MLE) to recommend probability distributions (e.g., Triangular, Gamma, Erlang) for raw input data.
* **Confidence Interval Calculation:** Determining the required number of replications to achieve a specific precision (Half-Width) for key performance indicators (KPIs).
* **Variance Reduction Analysis:** Assessing if steady-state conditions have been reached using warm-up period analysis.

## 2. Technical Reporting & Synthesis
### LaTeX Document Engineering
* **Template Orchestration:** Generating structured `.tex` files with automated sections for *Introduction*, *Model Logic*, *Verification & Validation*, and *Results*.
* **Dynamic Table Synthesis:** Converting exported Simio Pivot Tables and Experiment results into optimized LaTeX `tabular` or `booktabs` environments.
* **Equation Generation:** Automatically writing the LaTeX math notation for system distributions and queuing theory formulas (e.g., $L_q = \lambda W_q$).

### Visualization & Plotting
* **Performance Charting:** Generating PGFPlots or Matplotlib code for State-Variable plots, resource utilization histograms, and SMORE plots.
* **Flow Optimization Mapping:** Describing the critical path of entities through the system in professional technical prose.

## 3. Advanced Integration Skills
### API & External Toolchain
* **Simio API Interfacing:** Capability to interact with Simio’s C#/.NET based API for automated model execution and result extraction.
* **External Optimization:** Running heuristic algorithms (Genetic Algorithms, Bayesian Optimization) to find optimal model parameters beyond the native search tools.
* **Digital Twin Synchronization:** Connecting real-time data streams to Simio models for "What-If" operational forecasting.

## 4. Verification & Validation (V&V)
* **Little’s Law Consistency Check:** Running a logical verification script to ensure $L = \lambda W$ holds across steady-state segments.
* **Sensitivity Analysis:** Systematically varying input parameters to report on the stability and robustness of the proposed system improvements.
* **Boundary Condition Testing:** Simulating extreme-value scenarios to document system failure points and recovery times.