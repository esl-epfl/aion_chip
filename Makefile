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
TARGET       ?= rtl_sim_ghdl
TOOL         ?= ghdl
BUILD_DIR    ?= .build

TOPLEVEL     ?= tt_um_aion
SIM_MODULE   ?= $(CORE)_test
TEST_DIRS    ?= src/tb/

# Waveform Viewer - <surfer/gtkwave>
WAVEFORM_VIEWER ?= surfer

# ------------------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------------------
FUSESOC         := $(shell which fusesoc)
PYTHON          := $(shell which python)
VERIBLE_FORMAT  := $(shell which verible-verilog-format)
VSG             := $(shell which vsg)

# ------------------------------------------------------------------------------
# Dynamic Environment & Conda Path Fixes
# ------------------------------------------------------------------------------
ifdef CONDA_PREFIX
	export LD_LIBRARY_PATH := $(CONDA_PREFIX)/lib:$(LD_LIBRARY_PATH)
	export LD_PRELOAD      := $(CONDA_PREFIX)/lib/libpython3.12.so.1.0
endif

# ------------------------------------------------------------------------------
# Targets
# ------------------------------------------------------------------------------
.PHONY: all sim sim_verilator sim_all setup format clean waves synth pnr pnr_simple librelane openroad klayout librelane-openroad librelane-klayout _save_run _setup_cocotb_env

all: sim

sim: TARGET := rtl_sim_ghdl
sim: _setup_cocotb_env ## Run simulation (e.g., make sim CORE=aion TOOL=icarus) -> Default: TOOL=modelsim TARGET=rtl_sim CORE=systolic_array
	COCOTB_DUT_WRAPPED=0 \
	$(FUSESOC) run --build-root=$(BUILD_DIR) --target=$(TARGET) --tool=$(TOOL) $(CORE_NAME) $(PARAM_FLAGS)

sim_verilator: TARGET := rtl_sim_verilator
sim_verilator: TOOL := verilator
sim_verilator: _setup_cocotb_env ## Run simulation (e.g., make sim CORE=cgra TOOL=verilator) -> Default: TOOL=modelsim TARGET=rtl_sim CORE=systolic_array
	COCOTB_DUT_WRAPPED=1 \
	$(FUSESOC) run --build-root=$(BUILD_DIR) --target=$(TARGET) --tool=$(TOOL) $(CORE_NAME) $(PARAM_FLAGS)

sim_all: ## Run both icarus and verilator simulations and print a colored summary
	@$(PYTHON) $(PROJECT_ROOT)/scripts/sim_all.py

post_synth_sim: TARGET := post_synth_sim
post_synth_sim: TOOL := icarus
post_synth_sim: _setup_cocotb_env ## Run simulation (e.g., make sim CORE=cgra TOOL=verilator) -> Default: TOOL=modelsim TARGET=rtl_sim CORE=systolic_array
	COCOTB_DUT_WRAPPED=1 \
	$(FUSESOC) --verbose run --build-root=$(BUILD_DIR) --target=$(TARGET) --tool=$(TOOL) $(CORE_NAME) $(PARAM_FLAGS)

# --------------------------------------------------
# FuseSoc Setup & Clean
# --------------------------------------------------
setup:  ## Generate build files without running simulation/synthesis/... (e.g., make setup TARGET=rtl_sim CORE=cgra)
	$(FUSESOC) run --setup --build-root=$(BUILD_DIR) --target=$(TARGET) --tool=$(TOOL) $(CORE_NAME) $(PARAM_FLAGS)

format: ## Format the codebase
	@FILES=$$(find src -name '*.vhd*' 2>/dev/null); \
	if [ -n "$$FILES" ]; then \
		echo "Formatting files:"; \
		for f in $$FILES; do echo "  -> $$f"; done; \
		$(VSG) -f $$FILES --fix; \
	else \
		echo "No VHDL files found."; \
	fi

clean:  ## Clean up custom generated build directory
	rm -rf $(BUILD_DIR)/

clean-artifacts:  ## Clean up generated artifacts in src/misc/ and implementation/
	rm -rf src/misc/$(CORE).fst src/misc/$(CORE).gtkw src/misc/$(CORE).surf.ron
	rm -rf $(IMPL_DIR)/synth $(IMPL_DIR)/pnr $(IMPL_DIR)/pnr_simple

waves:
	@if [ "$(WAVEFORM_VIEWER)" = "gtkwave" ]; then \
		$(WAVEFORM_VIEWER) $(BUILD_DIR)/$(CORE).ghw src/misc/$(CORE).gtkw; \
	elif [ "$(WAVEFORM_VIEWER)" = "surfer" ]; then \
		$(WAVEFORM_VIEWER) -s src/misc/$(CORE).surf.ron $(BUILD_DIR)/$(CORE).fst; \
	else \
		surfer $(IP_DIR)/misc/$(CORE).fst; \
	fi

# ------------------------------------------------------------------------------
# Physical Implementation Flow (LibreLane via Docker)
#
#   make synth       RTL -> gate-level netlist + pre-PnR STA
#   make pnr         netlist + AI-generated cells -> GDS
#   make pnr_simple  RTL -> GDS, PDK standard cells only (the baseline)
#
# Pass LENIENT=1 to any of them to downgrade the hard checkers to warnings.
# ------------------------------------------------------------------------------
PROJECT_ROOT         ?= $(CURDIR)
PDK                  ?= ihp-sg13g2
PDK_ROOT             ?= /foss/pdks
LENIENT              ?= 0

IMPL_DIR              = $(PROJECT_ROOT)/implementation
LIBRELANE_CONFIG_SRC  = $(IMPL_DIR)/config.json
LIBRELANE_SDC         = $(IMPL_DIR)/constraints/aion.sdc
LIBRELANE_PIN_ORDER   = $(IMPL_DIR)/pin_order.cfg

# Directory of AI-generated cell views (LEF/LIB/GDS/Verilog/SPICE), consumed by
# `make pnr` only. Empty or absent means "PDK standard cells only".
CELLS_DIR            ?= $(IMPL_DIR)/cells

# Netlist `make pnr` hardens. Defaults to whatever `make synth` last saved;
# point it at the AI-rewritten netlist once the cell substitution has run.
NETLIST              ?= $(IMPL_DIR)/synth/nl/$(TOPLEVEL).nl.v

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

synth: ## Synthesis + pre-PnR STA -> implementation/synth/
	$(call librelane_prepare,$(SYNTH_RUN_DIR),synth,$(LENIENT_FLAG))
	$(call librelane_run,$(SYNTH_RUN_DIR),)
	@$(MAKE) --no-print-directory _save_run RUN_DIR=$(SYNTH_RUN_DIR) OUT_DIR=$(IMPL_DIR)/synth
	$(call librelane_finish,$(SYNTH_RUN_DIR))

pnr: ## PnR from a netlist plus the AI-generated cells (NETLIST=, CELLS_DIR=)
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
	@$(MAKE) --no-print-directory _save_run RUN_DIR=$(PNR_RUN_DIR) OUT_DIR=$(IMPL_DIR)/pnr
	$(call librelane_finish,$(PNR_RUN_DIR))

pnr_simple: ## Full RTL -> GDS flow with the PDK standard cells only
	$(call librelane_prepare,$(PNR_SIMPLE_RUN_DIR),pnr_simple,--pin-order $(LIBRELANE_PIN_ORDER) $(LENIENT_FLAG))
	$(call librelane_run,$(PNR_SIMPLE_RUN_DIR),)
	@$(MAKE) --no-print-directory _save_run RUN_DIR=$(PNR_SIMPLE_RUN_DIR) OUT_DIR=$(IMPL_DIR)/pnr_simple
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

librelane-openroad: openroad
librelane-klayout: klayout

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
		$(IMPL_DIR)/?*) ;; \
		*) echo "Error: refusing to clean OUT_DIR='$(OUT_DIR)' outside $(IMPL_DIR)/"; exit 1 ;; \
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
_setup_cocotb_env:
	$(eval export COCOTB_ANSI_OUTPUT     := 1)
	$(eval export PYGPI_PYTHON_BIN       := $(shell cocotb-config --python-bin))
	$(eval export COCOTB_TOPLEVEL        := $(TOPLEVEL))
	$(eval export COCOTB_TEST_MODULES    := $(SIM_MODULE))
	$(eval export PYTHONPATH             := $(shell realpath $(TEST_DIRS)):$(PYTHONPATH))

















# # Internal helpers for joining lists
# comma := ,
# space := $(subst ,, )
#
# PROJECT_ROOT  = $(shell pwd)
# IP_DIR        = $(PROJECT_ROOT)/src/modules/$(CORE)
# # IP_DIR        = $(PROJECT_ROOT)/src
#
# FUSESOC_BUILD_DIR  = $(strip $(shell find $(BUILD_DIR) -type d -name "aion_$(CORE)_*" 2>/dev/null | sort | head -n 1))
#
# # ------------------------------------------------------------------------------
# # LibreLane / Physical Implementation
# # ------------------------------------------------------------------------------
# PDK                  ?= ihp-sg13g2
# PDK_ROOT             ?= /foss/pdks
# LIBRELANE_RUN_DIR    := $(BUILD_DIR)/librelane_$(CORE)
# SYNTH_RUN_DIR        := $(BUILD_DIR)/synth_$(CORE)
# FLOW_FINAL_DIR        = $(LIBRELANE_RUN_DIR)/final
# LIBRELANE_CONFIG_SRC  = $(IP_DIR)/implementation/config.json
# LIBRELANE_CONFIG      = $(LIBRELANE_RUN_DIR)/config.json
# SYNTH_CONFIG          = $(SYNTH_RUN_DIR)/config.json
# LIBRELANE_PIN_ORDER   = $(IP_DIR)/implementation/pin_order.cfg
#
# # ------------------------------------------------------------------------------
# # Dynamic Environment & Conda Path Fixes
# # ------------------------------------------------------------------------------
# ifdef CONDA_PREFIX
# 	export LD_LIBRARY_PATH := $(CONDA_PREFIX)/lib:$(LD_LIBRARY_PATH)
# 	export LD_PRELOAD      := $(CONDA_PREFIX)/lib/libpython3.12.so.1.0
# endif
#
# # ------------------------------------------------------------------------------
# # Tools
# # ------------------------------------------------------------------------------
# FUSESOC         := $(shell which fusesoc)
# PYTHON          := $(shell which python)
# VERIBLE_FORMAT  := $(shell which verible-verilog-format)
#
# # ------------------------------------------------------------------------------
# # Targets
# # ------------------------------------------------------------------------------
# .PHONY: all sim sim_verilator sim_all setup setup_impl librelane synth save_synth format clean waves _setup_cocotb_env
#
# all: sim
#
# sim: TARGET := rtl_sim
# sim: _setup_cocotb_env ## Run simulation (e.g., make sim CORE=cgra TOOL=verilator) -> Default: TOOL=modelsim TARGET=rtl_sim CORE=systolic_array
# 	COCOTB_DUT_WRAPPED=0 \
# 	$(FUSESOC) run --build-root=$(BUILD_DIR) --target=$(TARGET) --tool=$(TOOL) $(CORE_NAME) $(PARAM_FLAGS)
#
# sim_verilator: TARGET := rtl_sim_verilator
# sim_verilator: TOOL := verilator
# sim_verilator: TOPLEVEL := top
# sim_verilator: _setup_cocotb_env ## Run simulation (e.g., make sim CORE=cgra TOOL=verilator) -> Default: TOOL=modelsim TARGET=rtl_sim CORE=systolic_array
# 	COCOTB_DUT_WRAPPED=1 \
# 	$(FUSESOC) run --build-root=$(BUILD_DIR) --target=$(TARGET) --tool=$(TOOL) $(CORE_NAME) $(PARAM_FLAGS)
#
# post_synth_sim: TARGET := post_synth_sim
# post_synth_sim: _setup_cocotb_env ## Run simulation (e.g., make sim CORE=cgra TOOL=verilator) -> Default: TOOL=modelsim TARGET=rtl_sim CORE=systolic_array
# 	COCOTB_DUT_WRAPPED=1 \
# 	$(FUSESOC) --verbose run --build-root=$(BUILD_DIR) --target=$(TARGET) --tool=$(TOOL) $(CORE_NAME) $(PARAM_FLAGS)
#
# sim_all: ## Run both icarus and verilator simulations and print a colored summary
# 	@$(PYTHON) $(PROJECT_ROOT)/utils/sim_all.py
#
# # ------------------------------------------------------------------------------
# # Physical Implementation Flow (LibreLane via Docker)
# # ------------------------------------------------------------------------------
# define librelane_prepare
# 	@mkdir -p $(1)/rtl $(2)
# 	@$(eval _RTL_FILES := $(shell $(PYTHON) $(PROJECT_ROOT)/utils/extract_fusesoc_sources.py \
# 		--core $(CORE_NAME) \
# 		--target librelane \
# 		--config $(PROJECT_ROOT)/fusesoc.conf \
# 		--build-root $(BUILD_DIR) \
# 		--format list))
# 	@for src in $(_RTL_FILES); do \
# 		dst="$(1)/rtl/$$(basename $$src)"; \
# 		if [ ! -e "$$dst" ] || [ "$$src" -nt "$$dst" ]; then \
# 			cp -v "$$src" "$$dst"; \
# 		fi; \
# 	done
# 	@$(PYTHON) $(PROJECT_ROOT)/utils/prepare_librelane_config.py \
# 		--src-config $(LIBRELANE_CONFIG_SRC) \
# 		--dst-config $(3) \
# 		--ip-dir $(IP_DIR) \
# 		--pin-order $(LIBRELANE_PIN_ORDER) \
# 		--verilog-files "$(subst $(space),$(comma),$(_RTL_FILES))" \
# 		$(4)
# endef
#
# librelane: ## Run LibreLane physical implementation flow inside $(BUILD_DIR)
# 	$(call librelane_prepare,$(LIBRELANE_RUN_DIR),$(FLOW_FINAL_DIR),$(LIBRELANE_CONFIG),)
# 	@cd $(LIBRELANE_RUN_DIR) && HOST_PWD=$(PROJECT_ROOT) $(PROJECT_ROOT)/run.sh librelane config.json \
# 		--pdk $(PDK) \
# 		--pdk-root $(PDK_ROOT) \
# 		--manual-pdk \
# 		--save-views-to ./final/
#
# # Latest LibreLane synthesis run directory (timestamped).
# SYNTH_LATEST_RUN := $(lastword $(sort $(wildcard $(SYNTH_RUN_DIR)/runs/RUN_*)))
#
# synth: ## Run LibreLane synthesis only inside $(BUILD_DIR)/synth_$(CORE)
# 	$(call librelane_prepare,$(SYNTH_RUN_DIR),$(SYNTH_RUN_DIR)/final,$(SYNTH_CONFIG),--synth-only)
# 	@cd $(SYNTH_RUN_DIR) && HOST_PWD=$(PROJECT_ROOT) $(PROJECT_ROOT)/run.sh librelane config.json \
# 		--pdk $(PDK) \
# 		--pdk-root $(PDK_ROOT) \
# 		--manual-pdk \
# 		--save-views-to ./final/
# 	@$(MAKE) _save_synth
#
# waves:
# 	@if [ "$(WAVEFORM_VIEWER)" = "gtkwave" ]; then \
# 		$(WAVEFORM_VIEWER) $(IP_DIR)/misc/$(CORE).fst $(IP_DIR)/misc/$(CORE).gtkw; \
# 	elif [ "$(WAVEFORM_VIEWER)" = "surfer" ]; then \
# 		$(WAVEFORM_VIEWER) -s $(IP_DIR)/misc/$(CORE).surf.ron $(IP_DIR)/misc/$(CORE).fst; \
# 	else \
# 		surfer $(IP_DIR)/misc/$(CORE).fst; \
# 	fi
#
# # ------------------------------------------------------------------------------
# # IP-Specific Configuration Auto-Loading
# # ------------------------------------------------------------------------------
# # We look for an optional 'config.mk' inside the active IP directory to auto-load
# # IP-specific parameters like TOPLEVEL, SIM_MODULE, or custom PARAM_FLAGS.
# -include $(IP_DIR)/config.mk
#
# # Fallback defaults if the IP doesn't provide a config.mk :(
# TOPLEVEL     ?= $(CORE)
# SIM_MODULE   ?= $(CORE)_test
# TEST_DIRS    ?= $(IP_DIR)/tb/
#
# # --------------------------------------------------
# # FuseSoc Setup & Clean
# # --------------------------------------------------
# setup:  ## Generate build files without running simulation/synthesis/... (e.g., make setup TARGET=rtl_sim CORE=cgra)
# 	$(FUSESOC) run --setup --build-root=$(BUILD_DIR) --target=$(TARGET) --tool=$(TOOL) $(CORE_NAME) $(PARAM_FLAGS)
#
# format: ## Format the codebase
# 	@FILES=$$(find src -name '*.sv*' 2>/dev/null); \
# 	if [ -n "$$FILES" ]; then \
# 		echo "Formatting files:"; \
# 		for f in $$FILES; do echo "  -> $$f"; done; \
# 		echo "$$FILES" | xargs $(VERIBLE_FORMAT) --flagfile=.verible-verilog-format --inplace; \
# 	else \
# 		echo "No SystemVerilog files found."; \
# 	fi
#
# clean:  ## Clean up custom generated build directory
# 	rm -rf $(BUILD_DIR)/
#
# # ------------------------------------------------------------------------------
# # Utils targets
# # ------------------------------------------------------------------------------
# _save_synth:
# 	@if [ -z "$(SYNTH_LATEST_RUN)" ]; then \
# 		echo "Error: no synthesis run found in $(SYNTH_RUN_DIR)/runs/"; \
# 		exit 1; \
# 	fi
# 	@mkdir -p $(IP_DIR)/implementation/synth/reports
# 	@echo "Copying synthesis artifacts from $(SYNTH_LATEST_RUN) to $(IP_DIR)/implementation/synth/"
# 	@cp -v $(SYNTH_LATEST_RUN)/final/nl/$(CORE).nl.v $(IP_DIR)/implementation/synth/
# 	@cp -v $(SYNTH_LATEST_RUN)/final/json_h/$(CORE).h.json $(IP_DIR)/implementation/synth/
# 	@cp -v $(SYNTH_LATEST_RUN)/final/metrics.csv $(IP_DIR)/implementation/synth/
# 	@cp -v $(SYNTH_LATEST_RUN)/final/metrics.json $(IP_DIR)/implementation/synth/
# 	@cp -v $(SYNTH_LATEST_RUN)/flow.log $(IP_DIR)/implementation/synth/
# 	@cp -v $(SYNTH_LATEST_RUN)/6-yosys-synthesis/reports/stat.rpt $(IP_DIR)/implementation/synth/reports/
# 	@cp -v $(SYNTH_LATEST_RUN)/6-yosys-synthesis/reports/latch.rpt $(IP_DIR)/implementation/synth/reports/
# 	@cp -v $(SYNTH_LATEST_RUN)/6-yosys-synthesis/reports/chk.rpt $(IP_DIR)/implementation/synth/reports/
# 	@cp -v $(SYNTH_LATEST_RUN)/6-yosys-synthesis/reports/pre_synth_chk.rpt $(IP_DIR)/implementation/synth/reports/
# 	@cp -v $(SYNTH_LATEST_RUN)/6-yosys-synthesis/reports/post_dff.rpt $(IP_DIR)/implementation/synth/reports/
# 	@echo "Synthesis artifacts saved to $(IP_DIR)/implementation/synth/"
#
# # ------------------------------------------------------------------------------
# # Cocotb Variable Export Routine
# # ------------------------------------------------------------------------------
# _setup_cocotb_env:
# 	$(eval export COCOTB_ANSI_OUTPUT     := 1)
# 	$(eval export PYGPI_PYTHON_BIN       := $(shell cocotb-config --python-bin))
# 	$(eval export COCOTB_TOPLEVEL        := $(TOPLEVEL))
# 	$(eval export COCOTB_TEST_MODULES    := $(SIM_MODULE))
# 	$(eval export PYTHONPATH             := $(shell realpath $(TEST_DIRS)):$(PYTHONPATH))
