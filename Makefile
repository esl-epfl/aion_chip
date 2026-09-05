# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-01
#  Description:               AION SoC - Makefile
# ================================================================

include scripts/utils.mk

# ------------------------------------------------------------------------------
# Default configurations
# ------------------------------------------------------------------------------
CORE         ?= aion
CORE_NAME     = epfl:aion:$(CORE):1.0.0
BUILD_DIR    ?= .build

TOPLEVEL     ?= tt_um_aion
TEST_DIRS    ?= src/tb/

# Where FuseSoC works for the current target, and so where that target's
# waveform lands.
SIM_WORK_DIR  = $(BUILD_DIR)/$(subst :,_,$(CORE_NAME))/$(TARGET)

PROJECT_ROOT ?= $(CURDIR)

# Waveform Viewer - <surfer/gtkwave>
WAVEFORM_VIEWER ?= surfer

# ------------------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------------------
FUSESOC := $(shell which fusesoc)
PYTHON  := $(shell which python)
VSG     := $(shell which vsg)

# ------------------------------------------------------------------------------
# Dynamic Environment & Conda Path Fixes
# ------------------------------------------------------------------------------
ifdef CONDA_PREFIX
	export LD_LIBRARY_PATH := $(CONDA_PREFIX)/lib:$(LD_LIBRARY_PATH)
	export LD_PRELOAD      := $(CONDA_PREFIX)/lib/libpython3.12.so.1.0
endif

.PHONY: all sim post_synth_sim post_pnr_sim post_pnr_sim_ai sim_all setup format \
        clean clean-impl clean-flow clean-all waves synth pnr pnr_simple librelane \
        openroad klayout _save_run _setup_cocotb_env _require_sdf

all: sim

# ==============================================================================
# Simulation
#
#   make sim             VHDL RTL, GHDL
#   make post_synth_sim  synthesis netlist, Verilator (TOOL=icarus for Icarus)
#   make post_pnr_sim    placed netlist + SDF, Icarus  -- delays, but not signoff
#   make sim_all         all of the above, with a summary table
#
# GHDL is the only simulator that reads the VHDL sources, so the RTL simulation
# has exactly one backend. Verilator and Icarus only ever see gate-level
# Verilog, which is why they appear from post-synthesis onwards and not before.
# ==============================================================================
# The simulation targets use the Edalize Flow API, which takes its simulator
# from the core file, so no --tool is passed here. TOOL only selects which of
# the per-simulator FuseSoC targets to run.
TARGET ?= rtl_sim
TOOL   ?= ghdl

# The SDF corner the post-PnR run annotates. The PnR flow writes one directory
# per corner. Which one to pick is a question about how much delay you want in
# the functional run, not about which one will catch a violation -- Icarus
# discards the SDF's TIMINGCHECK records (see post_pnr_sim below), so no corner
# can fail on setup or hold. Setup and hold are STA's job; see
# flow/pnr_simple/reports/*-openroad-stapostpnr/.
#   nom_typ_1p20V_25C | nom_fast_1p32V_m40C | nom_slow_1p08V_125C
# SDF_CORNER ?= nom_typ_1p20V_25C
SDF_CORNER ?= nom_slow_1p08V_125C
PNR_SDF     = $(FLOW_DIR)/pnr_simple/sdf/$(SDF_CORNER)/$(TOPLEVEL)__$(SDF_CORNER).sdf

# Dumping every net of a ~12k-cell netlist costs more than the simulation does.
# 1 = the DUT's own ports, which is what you want 95% of the time.
GATE_DUMPDEPTH ?= 1

# Only the Icarus targets declare the plusarg parameters (Verilator has no
# dump_waves module and no $sdf_annotate to feed), so gate the flag on the tool.
ICARUS_DUMP = $(if $(filter icarus,$(TOOL)),--dumpdepth=$(GATE_DUMPDEPTH),)

FUSESOC_RUN = $(FUSESOC) run --build-root=$(BUILD_DIR) --target=$(TARGET) $(CORE_NAME)

sim: TARGET := rtl_sim
sim: TOOL   := ghdl
sim: _setup_cocotb_env ## RTL simulation of the VHDL sources (GHDL)
	$(FUSESOC_RUN) $(PARAM_FLAGS)

post_synth_sim: TOOL   := verilator
post_synth_sim: TARGET  = $(if $(filter icarus,$(TOOL)),post_synth_sim_icarus,post_synth_sim)
post_synth_sim: _setup_cocotb_env ## Post-synthesis gate-level sim, no timing (TOOL=verilator|icarus)
	@if [ ! -f "$(FLOW_DIR)/synth/nl/$(TOPLEVEL).nl.v" ]; then \
		echo "Error: no synthesis netlist at $(FLOW_DIR)/synth/nl/$(TOPLEVEL).nl.v"; \
		echo "       run 'make synth' first."; \
		exit 1; \
	fi
	$(FUSESOC_RUN) $(ICARUS_DUMP) $(PARAM_FLAGS)

post_pnr_sim: TOOL   := icarus
post_pnr_sim: TARGET  = $(if $(filter verilator,$(TOOL)),post_pnr_sim_verilator,post_pnr_sim)
post_pnr_sim: _setup_cocotb_env ## Post-PnR gate-level sim with SDF delays (TOOL=verilator drops the delays)
	@if [ ! -f "$(FLOW_DIR)/pnr_simple/nl/$(TOPLEVEL).nl.v" ]; then \
		echo "Error: no post-PnR netlist at $(FLOW_DIR)/pnr_simple/nl/$(TOPLEVEL).nl.v"; \
		echo "       run 'make pnr_simple' first."; \
		exit 1; \
	fi
ifeq ($(TOOL),verilator)
	@echo "Warning: Verilator ignores \$$sdf_annotate. This run has NO timing;"
	@echo "         it only re-checks the function of the placed netlist."
	$(FUSESOC_RUN) $(PARAM_FLAGS)
else
	@$(MAKE) --no-print-directory _require_sdf SDF=$(PNR_SDF)
	$(FUSESOC_RUN) --sdf_file=$(PNR_SDF) $(ICARUS_DUMP) $(PARAM_FLAGS)
endif

post_pnr_sim_ai: TARGET := post_pnr_sim_ai
post_pnr_sim_ai: TOOL   := icarus
post_pnr_sim_ai: SDF     = $(FLOW_DIR)/pnr/sdf/$(SDF_CORNER)/$(TOPLEVEL)__$(SDF_CORNER).sdf
post_pnr_sim_ai: _setup_cocotb_env ## Post-PnR sim of the AI-cell flow (output of `make pnr`)
	@$(MAKE) --no-print-directory _require_sdf SDF=$(SDF)
	$(FUSESOC_RUN) --sdf_file=$(SDF) $(ICARUS_DUMP) $(PARAM_FLAGS)

sim_all: ## Run every simulation stage and print a summary table
	@$(PYTHON) $(PROJECT_ROOT)/scripts/sim_all.py

_require_sdf:
	@if [ ! -f "$(SDF)" ]; then \
		echo "Error: SDF not found: $(SDF)"; \
		echo "       available corners:"; \
		ls -1 "$(dir $(patsubst %/,%,$(dir $(SDF))))" 2>/dev/null | sed 's/^/         /' || echo "         (none — run the PnR flow first)"; \
		exit 1; \
	fi

# --------------------------------------------------
# FuseSoc Setup & Clean
# --------------------------------------------------
setup:  ## Generate build files without running (e.g. make setup TARGET=post_pnr_sim)
	$(FUSESOC) run --setup --build-root=$(BUILD_DIR) --target=$(TARGET) $(CORE_NAME) $(PARAM_FLAGS)

format: ## Format the codebase
	@FILES=$$(find src -name '*.vhd*' 2>/dev/null); \
	if [ -n "$$FILES" ]; then \
		echo "Formatting files:"; \
		for f in $$FILES; do echo "  -> $$f"; done; \
		$(VSG) -f $$FILES --fix; \
	else \
		echo "No VHDL files found."; \
	fi

# $(BUILD_DIR) holds two very different things: the FuseSoC simulation builds,
# which are cheap to redo, and the LibreLane run directories, which are hours
# of work and are what `make openroad` opens. They get separate targets so a
# routine `make clean` cannot throw away a hardening run.
SIM_BUILD_DIRS  = $(BUILD_DIR)/$(subst :,_,$(CORE_NAME)) $(BUILD_DIR)/sim_all_*.log
IMPL_BUILD_DIRS = $(SYNTH_RUN_DIR) $(PNR_RUN_DIR) $(PNR_SIMPLE_RUN_DIR)

clean:  ## Remove the simulation builds and the __pycache__ dirs (keeps the LibreLane runs)
	rm -rf $(SIM_BUILD_DIRS)
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

clean-impl:  ## Remove the LibreLane run directories (hours of work — be sure)
	rm -rf $(IMPL_BUILD_DIRS)

clean-flow:  ## Remove the saved physical-implementation artifacts under flow/
	rm -rf $(FLOW_DIR)/synth $(FLOW_DIR)/pnr $(FLOW_DIR)/pnr_simple

clean-all: clean clean-impl clean-flow  ## All three of the above

waves: ## Open a stage's trace (make waves TARGET=post_pnr_sim)
	@wave=$(SIM_WORK_DIR)/aion.fst; \
	if [ ! -f "$$wave" ]; then \
		echo "Error: no trace at $$wave"; \
		echo "       run the '$(TARGET)' target first, or pass TARGET=<fusesoc target>."; \
		exit 1; \
	fi; \
	$(WAVEFORM_VIEWER) "$$wave"

# ==============================================================================
# Physical Implementation Flow (LibreLane via Docker)
#
#   make synth       RTL -> gate-level netlist + pre-PnR STA
#   make pnr         netlist + AI-generated cells -> GDS
#   make pnr_simple  RTL -> GDS, PDK standard cells only (the baseline)
#
# Each target works in $(BUILD_DIR)/<target>_$(CORE)/ and then copies the views,
# metrics and reports of its latest run into flow/<target>/, which is the fixed
# location flow/*.core points the simulation targets at.
#
# Pass LENIENT=1 to any of them to downgrade the hard checkers to warnings.
# ==============================================================================
PDK                  ?= ihp-sg13g2
PDK_ROOT             ?= /foss/pdks
LENIENT              ?= 0

# Inputs: hand-written, tracked.
IMPL_DIR              = $(PROJECT_ROOT)/implementation
LIBRELANE_CONFIG_SRC  = $(IMPL_DIR)/config.json
LIBRELANE_SDC         = $(IMPL_DIR)/constraints/aion.sdc
LIBRELANE_PIN_ORDER   = $(IMPL_DIR)/pin_order.cfg

# Outputs: generated, git-ignored apart from the .core files.
FLOW_DIR              = $(PROJECT_ROOT)/flow

# Directory of AI-generated cell views (LEF/LIB/GDS/Verilog/SPICE), consumed by
# `make pnr` only. Empty or absent means "PDK standard cells only".
CELLS_DIR            ?= $(IMPL_DIR)/cells

# Netlist `make pnr` hardens. Defaults to whatever `make synth` last saved;
# point it at the AI-rewritten netlist once the cell substitution has run.
NETLIST              ?= $(FLOW_DIR)/synth/nl/$(TOPLEVEL).nl.v

SYNTH_RUN_DIR        := $(BUILD_DIR)/synth_$(CORE)
PNR_RUN_DIR          := $(BUILD_DIR)/pnr_$(CORE)
PNR_SIMPLE_RUN_DIR   := $(BUILD_DIR)/pnr_simple_$(CORE)

# Run directory the GUI targets open. Override to inspect a different run,
# e.g. `make openroad VIEW_RUN_DIR=.build/pnr_aion`.
VIEW_RUN_DIR         ?= $(PNR_SIMPLE_RUN_DIR)

LENIENT_FLAG         := $(if $(filter-out 0,$(LENIENT)),--lenient,)

# Extra flags forwarded to librelane, e.g. LIBRELANE_ARGS="--to OpenROAD.Floorplan"
LIBRELANE_ARGS       ?=

# Internal helpers for joining lists
comma := ,
space := $(subst ,, )

# $(1) run directory, $(2) mode, $(3) extra flags for the prepare script
define librelane_prepare
	@mkdir -p $(1)/rtl $(1)/final
	@$(eval _RTL_FILES := $(shell $(PYTHON) $(PROJECT_ROOT)/scripts/extract_fusesoc_sources.py \
		--core $(CORE_NAME) \
		--target librelane \
		--config $(PROJECT_ROOT)/fusesoc.conf \
		--build-root $(BUILD_DIR) \
		--format list))
	@for src in $(_RTL_FILES); do \
		dst="$(1)/rtl/$$(basename $$src)"; \
		if [ ! -e "$$dst" ] || [ "$$src" -nt "$$dst" ]; then \
			cp -v "$$src" "$$dst"; \
		fi; \
	done
	@$(PYTHON) $(PROJECT_ROOT)/scripts/prepare_librelane_config.py \
		--src-config $(LIBRELANE_CONFIG_SRC) \
		--dst-config $(1)/config.json \
		--ip-dir $(1) \
		--project-root $(PROJECT_ROOT) \
		--mode $(2) \
		--sdc $(LIBRELANE_SDC) \
		--vhdl-files "$(subst $(space),$(comma),$(addprefix $(1)/rtl/,$(notdir $(_RTL_FILES))))" \
		$(3)
endef

# $(1) run directory, $(2) extra flags for librelane
#
# The exit status is stashed rather than propagated, so that a flow which dies
# late still gets its reports collected. librelane_finish re-raises it.
define librelane_run
	@( cd $(1) && HOST_PWD=$(PROJECT_ROOT) $(PROJECT_ROOT)/scripts/docker_run.sh librelane config.json \
		--pdk $(PDK) \
		--pdk-root $(PDK_ROOT) \
		--manual-pdk \
		--save-views-to ./final/ \
		$(2) $(LIBRELANE_ARGS) ); \
	status=$$?; \
	echo $$status > $(1)/.exit_status; \
	if [ $$status -ne 0 ]; then \
		echo "LibreLane exited with status $$status — collecting artifacts anyway."; \
	fi
endef

# $(1) run directory
define librelane_finish
	@status=$$(cat $(1)/.exit_status 2>/dev/null || echo 1); \
	if [ "$$status" -ne 0 ]; then \
		echo "LibreLane failed (exit $$status); the artifacts above are from that failed run."; \
	fi; \
	exit $$status
endef

synth: ## Synthesis + pre-PnR STA -> flow/synth/
	$(call librelane_prepare,$(SYNTH_RUN_DIR),synth,$(LENIENT_FLAG))
	$(call librelane_run,$(SYNTH_RUN_DIR),)
	@$(MAKE) --no-print-directory _save_run RUN_DIR=$(SYNTH_RUN_DIR) OUT_DIR=$(FLOW_DIR)/synth
	$(call librelane_finish,$(SYNTH_RUN_DIR))

pnr: ## PnR from a netlist plus the AI-generated cells (NETLIST=, CELLS_DIR=) -> flow/pnr/
	@$(PYTHON) $(PROJECT_ROOT)/scripts/collect_cells.py $(CELLS_DIR) $(LENIENT_FLAG)
	@if [ ! -f "$(NETLIST)" ]; then \
		echo "Error: netlist not found: $(NETLIST)"; \
		echo "       run 'make synth' first, or pass NETLIST=<path/to/netlist.v>"; \
		exit 1; \
	fi
	@mkdir -p $(PNR_RUN_DIR)/nl
	@cp -v $(NETLIST) $(PNR_RUN_DIR)/nl/$(TOPLEVEL).nl.v
	$(call librelane_prepare,$(PNR_RUN_DIR),pnr,--pin-order $(LIBRELANE_PIN_ORDER) --cells-dir $(CELLS_DIR) $(LENIENT_FLAG))
	$(call librelane_run,$(PNR_RUN_DIR),--from Checker.NetlistAssignStatements -e nl=nl/$(TOPLEVEL).nl.v)
	@$(MAKE) --no-print-directory _save_run RUN_DIR=$(PNR_RUN_DIR) OUT_DIR=$(FLOW_DIR)/pnr
	$(call librelane_finish,$(PNR_RUN_DIR))

pnr_simple: ## Full RTL -> GDS flow with the PDK standard cells only -> flow/pnr_simple/
	$(call librelane_prepare,$(PNR_SIMPLE_RUN_DIR),pnr_simple,--pin-order $(LIBRELANE_PIN_ORDER) $(LENIENT_FLAG))
	$(call librelane_run,$(PNR_SIMPLE_RUN_DIR),)
	@$(MAKE) --no-print-directory _save_run RUN_DIR=$(PNR_SIMPLE_RUN_DIR) OUT_DIR=$(FLOW_DIR)/pnr_simple
	$(call librelane_finish,$(PNR_SIMPLE_RUN_DIR))

librelane: pnr_simple ## Alias for pnr_simple

openroad: ## Open the last run in the OpenROAD GUI (VIEW_RUN_DIR=)
	@cd $(VIEW_RUN_DIR) && HOST_PWD=$(PROJECT_ROOT) $(PROJECT_ROOT)/scripts/docker_run.sh librelane config.json \
		--pdk $(PDK) \
		--pdk-root $(PDK_ROOT) \
		--manual-pdk \
		--last-run \
		--flow OpenInOpenROAD

klayout: ## Open the last run in KLayout (VIEW_RUN_DIR=)
	@cd $(VIEW_RUN_DIR) && HOST_PWD=$(PROJECT_ROOT) $(PROJECT_ROOT)/scripts/docker_run.sh librelane config.json \
		--pdk $(PDK) \
		--pdk-root $(PDK_ROOT) \
		--manual-pdk \
		--last-run \
		--flow OpenInKLayout

# ------------------------------------------------------------------------------
# Utils targets
# ------------------------------------------------------------------------------
# Copy the views, metrics and reports of the latest run in RUN_DIR to OUT_DIR.
_save_run:
	@run=$$(ls -d $(RUN_DIR)/runs/RUN_* 2>/dev/null | sort | tail -n 1); \
	if [ -z "$$run" ]; then \
		echo "Error: no run found in $(RUN_DIR)/runs/"; \
		exit 1; \
	fi; \
	case "$(OUT_DIR)" in \
		$(FLOW_DIR)/?*) ;; \
		*) echo "Error: refusing to clean OUT_DIR='$(OUT_DIR)' outside $(FLOW_DIR)/"; exit 1 ;; \
	esac; \
	echo "Saving artifacts from $$run to $(OUT_DIR)/"; \
	rm -rf $(OUT_DIR); \
	mkdir -p $(OUT_DIR)/reports $(OUT_DIR)/logs; \
	if [ -d "$$run/final" ]; then cp -r "$$run/final/." $(OUT_DIR)/; fi; \
	for f in flow.log resolved.json; do \
		[ -f "$$run/$$f" ] && cp "$$run/$$f" $(OUT_DIR)/logs/; \
	done; \
	find "$$run" -mindepth 2 -name '*.rpt' -type f | while read -r rpt; do \
		rel=$$(printf '%s' "$${rpt#$$run/}" | sed 's|/reports/|/|'); \
		mkdir -p "$(OUT_DIR)/reports/$$(dirname "$$rel")"; \
		cp "$$rpt" "$(OUT_DIR)/reports/$$rel"; \
	done; \
	echo "Artifacts saved to $(OUT_DIR)/"

# ------------------------------------------------------------------------------
# Cocotb Variable Export Routine
# ------------------------------------------------------------------------------
# COCOTB_TEST_MODULES and PYGPI_PYTHON_BIN are set by the Edalize `sim` flow
# from `cocotb_module` in aion.core, so they are deliberately not set here.
# COCOTB_TOPLEVEL is: the Icarus runs elaborate two or three root modules and
# cocotb has to be told which one is the DUT.
_setup_cocotb_env:
	$(eval export COCOTB_ANSI_OUTPUT := 1)
	$(eval export COCOTB_TOPLEVEL    := $(TOPLEVEL))
	$(eval export PYTHONPATH         := $(shell realpath $(TEST_DIRS)):$(PYTHONPATH))
