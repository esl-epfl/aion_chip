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

.PHONY: all sim post_synth_sim post_synth_sim_ai post_pnr_sim post_pnr_sim_ai sim_all setup format \
        clean clean-impl clean-flow clean-all waves synth pnr pnr_simple librelane \
        openroad klayout logo _save_run _setup_cocotb_env _require_sdf _check_sim_results \
        flow flow-status flow-list universal universal-update

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
PNR_SDF     = $(PNR_SIMPLE_OUT_DIR)/sdf/$(SDF_CORNER)/$(TOPLEVEL)__$(SDF_CORNER).sdf

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
	@$(MAKE) --no-print-directory _check_sim_results TARGET=$(TARGET)

post_synth_sim: TOOL   := verilator
post_synth_sim: TARGET  = $(if $(filter icarus,$(TOOL)),post_synth_sim_icarus,post_synth_sim)
post_synth_sim: _setup_cocotb_env ## Post-synthesis gate-level sim, no timing (TOOL=verilator|icarus)
	@if [ ! -f "$(SYNTH_OUT_DIR)/nl/$(TOPLEVEL).nl.v" ]; then \
		echo "Error: no synthesis netlist at $(SYNTH_OUT_DIR)/nl/$(TOPLEVEL).nl.v"; \
		echo "       run 'make synth' first."; \
		exit 1; \
	fi
	$(FUSESOC_RUN) $(ICARUS_DUMP) $(PARAM_FLAGS)
	@$(MAKE) --no-print-directory _check_sim_results TARGET=$(TARGET)

post_synth_sim_ai: TOOL   := verilator
post_synth_sim_ai: TARGET  = $(if $(filter icarus,$(TOOL)),post_synth_sim_ai_icarus,post_synth_sim_ai)
post_synth_sim_ai: _setup_cocotb_env ## Post-synthesis sim of the AION-cell netlist (output of step 3)
	@if [ ! -f "$(REWRITE_OUT_DIR)/nl/$(TOPLEVEL).nl.v" ]; then \
		echo "Error: no rewritten netlist at $(REWRITE_OUT_DIR)/nl/$(TOPLEVEL).nl.v"; \
		echo "       run 'python flow.py 3' first."; \
		exit 1; \
	fi
	$(FUSESOC_RUN) $(ICARUS_DUMP) $(PARAM_FLAGS)
	@$(MAKE) --no-print-directory _check_sim_results TARGET=$(TARGET)

post_pnr_sim: TOOL   := icarus
post_pnr_sim: TARGET  = $(if $(filter verilator,$(TOOL)),post_pnr_sim_verilator,post_pnr_sim)
post_pnr_sim: _setup_cocotb_env ## Post-PnR gate-level sim with SDF delays (TOOL=verilator drops the delays)
	@if [ ! -f "$(PNR_SIMPLE_OUT_DIR)/nl/$(TOPLEVEL).nl.v" ]; then \
		echo "Error: no post-PnR netlist at $(PNR_SIMPLE_OUT_DIR)/nl/$(TOPLEVEL).nl.v"; \
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
	@$(MAKE) --no-print-directory _check_sim_results TARGET=$(TARGET)

post_pnr_sim_ai: TOOL   := icarus
post_pnr_sim_ai: TARGET  = $(if $(filter verilator,$(TOOL)),post_pnr_sim_ai_verilator,post_pnr_sim_ai)
post_pnr_sim_ai: SDF     = $(PNR_OUT_DIR)/sdf/$(SDF_CORNER)/$(TOPLEVEL)__$(SDF_CORNER).sdf
post_pnr_sim_ai: _setup_cocotb_env ## Post-PnR sim of the AI-cell flow (TOOL=verilator drops the delays)
ifeq ($(TOOL),verilator)
	@echo "Warning: Verilator ignores \$$sdf_annotate. This run has NO timing;"
	@echo "         it only re-checks the function of the AI-cell netlist."
	$(FUSESOC_RUN) $(PARAM_FLAGS)
else
	@$(MAKE) --no-print-directory _require_sdf SDF=$(SDF)
	$(FUSESOC_RUN) --sdf_file=$(SDF) $(ICARUS_DUMP) $(PARAM_FLAGS)
endif
	@$(MAKE) --no-print-directory _check_sim_results TARGET=$(TARGET)

sim_all: ## Run every simulation stage and print a summary table
	@$(PYTHON) $(PROJECT_ROOT)/scripts/sim_all.py

# cocotb reports a failing test by printing FAIL and writing <failure/> into
# results.xml -- and then the simulator exits 0 anyway. Verified: a testbench
# that does nothing but `assert False` gives TESTS=1 PASS=0 FAIL=1 and
# `make post_synth_sim` still exits 0. Without this check every simulation
# target is decorative, and so is anything built on top of them.
_check_sim_results:
	@xml="$(SIM_WORK_DIR)/results.xml"; \
	if [ ! -f "$$xml" ]; then \
		echo "Error: the simulation wrote no cocotb results at $$xml"; \
		echo "       the run did not reach the testbench -- read the log above."; \
		exit 1; \
	fi; \
	total=$$(grep -c '<testcase ' "$$xml" 2>/dev/null || true); \
	bad=$$(grep -c -E '<(failure|error)[ />]' "$$xml" 2>/dev/null || true); \
	if [ "$${total:-0}" -eq 0 ]; then \
		echo "Error: $$xml records no test cases at all."; \
		exit 1; \
	fi; \
	if [ "$${bad:-0}" -ne 0 ]; then \
		echo "Error: $$bad of $$total cocotb test(s) FAILED ($$xml)."; \
		exit 1; \
	fi; \
	echo "cocotb: $$total/$$total test(s) passed"

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

clean-flow:  ## Remove every flow step's output under flow/ (keeps the .core files)
	rm -rf $(FLOW_DIR)/[1-7]_* $(FLOW_DIR)/pnr_simple $(FLOW_DIR)/logs \
	       $(FLOW_DIR)/coherence.json
	@# flow/synth and flow/pnr are where `make synth` and `make pnr` wrote
	@# before the step directories were numbered. A tree that predates that
	@# rename still has them, and nothing else would ever remove them.
	@rm -rf $(FLOW_DIR)/synth $(FLOW_DIR)/pnr

clean-all: clean clean-impl clean-flow  ## All three of the above
	rm -rf $(BUILD_DIR)

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

# Pin placement. Empty by default, so pin_order.cfg spreads the pins over all
# four sides -- what a macro instantiated in a parent needs. Setting
# LIBRELANE_DEF_TEMPLATE (e.g. to $(IMPL_DIR)/def/tt_block_$(TT_TILES)_pgvdd.def)
# switches to TinyTapeout's floorplan instead, which pins all 43 pins to the
# north edge for the multiplexer; prepare_librelane_config.py then ignores
# --pin-order. DIE_AREA in implementation/config.json has to match.
TT_TILES             ?= 4x2
LIBRELANE_DEF_TEMPLATE ?=
LIBRELANE_FLOORPLAN    = $(if $(LIBRELANE_DEF_TEMPLATE),\
                             --def-template $(LIBRELANE_DEF_TEMPLATE),\
                             --pin-order $(LIBRELANE_PIN_ORDER))

# Outputs: generated, git-ignored apart from the .core files.
FLOW_DIR              = $(PROJECT_ROOT)/flow

# Where each hardening target saves its views. One directory per flow step,
# numbered so `flow/` reads in the order the steps run; the flow handler
# (scripts/flow/, driven by ./flow.py) overrides them to the same values, so
# these targets behave identically whether you run them by hand or from it.
# _save_run refuses an OUT_DIR outside $(FLOW_DIR), so keep them under it.
SYNTH_OUT_DIR        ?= $(FLOW_DIR)/1_synth
REWRITE_OUT_DIR      ?= $(FLOW_DIR)/3_rewrite
PNR_OUT_DIR          ?= $(FLOW_DIR)/7_pnr
PNR_SIMPLE_OUT_DIR   ?= $(FLOW_DIR)/pnr_simple

# Directory of AI-generated cell views (LEF/LIB/GDS/Verilog/SPICE), consumed by
# `make pnr` only. Empty or absent means "PDK standard cells only".
CELLS_DIR            ?= $(IMPL_DIR)/cells

# The chip's logo, drawn as a macro on a top metal by `make logo`, and
# instantiated by tt_um_aion as an unbound component so it lands in the netlist
# for Odb.ManualMacroPlacement to place. LOGO_CELL is lower case because that is
# what GHDL makes of a VHDL identifier, and the netlist name has to match the
# LEF macro exactly. LOGO_WIDTH is
# the width of the *picture* in microns -- canvas and blank border included, so
# the macro keeps the proportions the picture has; the height follows its aspect
# ratio, and the raster pixel follows LOGO_LAYER's minimum width and spacing.
#
#   make logo LOGO_ARGS=--crop                 the strokes alone, no border
#   make logo LOGO_ARGS="--invert --recog"     the background, letters cut out
#
# --recog is what keeps the inverted plate out of the slit rules; without it
# --strict refuses the run. `make logo LOGO_ARGS=--help` lists the rest.
MACROS_DIR           ?= $(IMPL_DIR)/macros
LOGO_PNG             ?= $(PROJECT_ROOT)/logo/AION_Logo_CorrectSize.png
LOGO_CELL            ?= aion_logo
LOGO_LAYER           ?= TopMetal1
LOGO_WIDTH           ?= 640

# COVER, not BLOCK: the art is on TopMetal1 and the standard cells are on
# Metal1/Metal2, so nothing stops the placer filling the area underneath -- and
# a BLOCK macro this size would sterilise a large slice of a 4x2 tile. The LEF's
# OBS still covers the footprint, so the router leaves TopMetal1 there alone.
LOGO_CLASS           ?= COVER

# `make logo` only draws the macro now; nothing merges it into a hardened GDS
# automatically, because this design is about to become one of two macros in a
# parent and the logo belongs to the parent. To place it by hand:
#
#   python3 scripts/merge_logo.py <die>.gds implementation/macros/aion_logo.gds \
#       --at X,Y --design-top tt_um_aion --lef <die>.lef
#
# It carves the art around whatever is already on LOGO_LAYER, so it is safe to
# run on a die whose PDN straps cross the logo. See scripts/merge_logo.py.

# Netlist `make pnr` hardens. Defaults to whatever `make synth` last saved;
# point it at the AI-rewritten netlist once the cell substitution has run.
NETLIST              ?= $(SYNTH_OUT_DIR)/nl/$(TOPLEVEL).nl.v

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

synth: ## Synthesis + pre-PnR STA -> flow/1_synth/
	$(call librelane_prepare,$(SYNTH_RUN_DIR),synth,$(LENIENT_FLAG))
	$(call librelane_run,$(SYNTH_RUN_DIR),)
	@$(MAKE) --no-print-directory _save_run RUN_DIR=$(SYNTH_RUN_DIR) OUT_DIR=$(SYNTH_OUT_DIR)
	$(call librelane_finish,$(SYNTH_RUN_DIR))

pnr: ## PnR from a netlist plus the AI-generated cells (NETLIST=, CELLS_DIR=) -> flow/7_pnr/
	@$(PYTHON) $(PROJECT_ROOT)/scripts/collect_cells.py $(CELLS_DIR) $(LENIENT_FLAG)
	@if [ ! -f "$(NETLIST)" ]; then \
		echo "Error: netlist not found: $(NETLIST)"; \
		echo "       run 'make synth' first, or pass NETLIST=<path/to/netlist.v>"; \
		exit 1; \
	fi
	@mkdir -p $(PNR_RUN_DIR)/nl
	@cp -v $(NETLIST) $(PNR_RUN_DIR)/nl/$(TOPLEVEL).nl.v
	$(call librelane_prepare,$(PNR_RUN_DIR),pnr,$(LIBRELANE_FLOORPLAN) --cells-dir $(CELLS_DIR) $(LENIENT_FLAG))
	$(call librelane_run,$(PNR_RUN_DIR),--from Checker.NetlistAssignStatements -e nl=nl/$(TOPLEVEL).nl.v)
	@$(MAKE) --no-print-directory _save_run RUN_DIR=$(PNR_RUN_DIR) OUT_DIR=$(PNR_OUT_DIR)
	$(call librelane_finish,$(PNR_RUN_DIR))

pnr_simple: ## Full RTL -> GDS flow with the PDK standard cells only -> flow/pnr_simple/
	$(call librelane_prepare,$(PNR_SIMPLE_RUN_DIR),pnr_simple,$(LIBRELANE_FLOORPLAN) $(LENIENT_FLAG))
	$(call librelane_run,$(PNR_SIMPLE_RUN_DIR),)
	@$(MAKE) --no-print-directory _save_run RUN_DIR=$(PNR_SIMPLE_RUN_DIR) OUT_DIR=$(PNR_SIMPLE_OUT_DIR)
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

logo: ## Draw the logo as a GDS + LEF macro -> implementation/macros/
	@$(PYTHON) $(PROJECT_ROOT)/scripts/logo_to_gds.py $(LOGO_PNG) \
		--outdir $(MACROS_DIR) \
		--cell $(LOGO_CELL) \
		--layer $(LOGO_LAYER) \
		--width $(LOGO_WIDTH) \
		--lef-class $(LOGO_CLASS) \
		--strict $(LOGO_ARGS)

# ==============================================================================
# AION flow handler
#
# The nine-step chain that turns the RTL into a chip built from AI-generated
# standard cells. Each step is runnable on its own and writes under flow/;
# flow/coherence.json records what ran when, so a re-run of an early step
# shows up as STALE downstream instead of quietly producing a chip that mixes
# results from two different inputs.
#
#   make flow                    every step
#   make flow STEP=3             one step  (also 'STEP=2..5')
#   make flow STEP=6 FLOW_ARGS=--draw=auto
#   make flow STEP=6 FLOW_ARGS="--draw=auto -j 3"   three cells at a time
#   make flow-status             the coherence table
#
# ./flow.py is the same thing with a nicer command line.
# ==============================================================================
STEP      ?=
FLOW_ARGS ?=

flow: ## Run the AION flow (STEP=<n|range>, FLOW_ARGS=<extra flags>)
	@$(PYTHON) $(PROJECT_ROOT)/flow.py $(STEP) $(FLOW_ARGS)

flow-status: ## Per-step timestamps and staleness
	@$(PYTHON) $(PROJECT_ROOT)/flow.py status

flow-list: ## List the flow steps
	@$(PYTHON) $(PROJECT_ROOT)/flow.py --list

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
# Posit reference model (Stillwater Universal)
# ------------------------------------------------------------------------------
# The cocotb testbenches check the DUT against Universal, the reference posit
# implementation, rather than against a model written here. Universal is a
# header-only C++20 template library with no Python bindings, so src/tb/
# carries a small `extern "C"` shim and src/tb/posit.py is ctypes over it.
#
# Only include/ is fetched (a sparse, blobless checkout: ~12 MB against ~790 MB
# for the full tree, which is almost entirely docs) and it is pinned to a
# release tag so a reference model never changes under a design.
#
#   make universal            fetch + build (idempotent; the sim targets call it)
#   make universal-update     re-fetch after changing UNIVERSAL_VERSION
#   UNIVERSAL_ROOT=/usr/local make universal   use an installed copy instead
# ------------------------------------------------------------------------------
UNIVERSAL_VERSION ?= v4.10.1
UNIVERSAL_REPO    ?= https://github.com/stillwater-sc/universal.git
UNIVERSAL_DIR     ?= $(BUILD_DIR)/universal
UNIVERSAL_ROOT    ?= $(UNIVERSAL_DIR)
UNIVERSAL_INCLUDE  = $(UNIVERSAL_ROOT)/include/sw
UNIVERSAL_SHIM     = $(PROJECT_ROOT)/src/tb/universal_posit.cpp
UNIVERSAL_LIB      = $(PROJECT_ROOT)/src/tb/libuniversal_posit.so

CXX      ?= g++
CXXFLAGS ?= -O2 -std=c++20 -Wall -Wextra

.PHONY: universal universal-update

universal: $(UNIVERSAL_LIB) ## Fetch Universal and build the posit reference shim

$(UNIVERSAL_INCLUDE)/universal/number/posit/posit.hpp:
	@echo "Fetching Universal $(UNIVERSAL_VERSION) (headers only)"
	@rm -rf $(UNIVERSAL_DIR)
	@mkdir -p $(UNIVERSAL_DIR)
	@cd $(UNIVERSAL_DIR) && \
		git init -q . && \
		git remote add origin $(UNIVERSAL_REPO) && \
		git sparse-checkout init --cone >/dev/null && \
		git sparse-checkout set include >/dev/null && \
		git -c protocol.version=2 fetch --depth 1 --filter=blob:none \
			origin refs/tags/$(UNIVERSAL_VERSION) >/dev/null 2>&1 && \
		git checkout -q FETCH_HEAD
	@test -f $@ || { echo "Error: $@ missing after fetch"; exit 1; }

$(UNIVERSAL_LIB): $(UNIVERSAL_SHIM) $(UNIVERSAL_INCLUDE)/universal/number/posit/posit.hpp
	@echo "Building $(notdir $@) against Universal $(UNIVERSAL_VERSION)"
	@$(CXX) $(CXXFLAGS) -shared -fPIC -I$(UNIVERSAL_INCLUDE) \
		-DUNIVERSAL_VERSION_STRING='"$(UNIVERSAL_VERSION)"' \
		$< -o $@

universal-update: ## Re-fetch Universal at UNIVERSAL_VERSION and rebuild
	@rm -rf $(UNIVERSAL_DIR) $(UNIVERSAL_LIB)
	@$(MAKE) --no-print-directory universal

# ------------------------------------------------------------------------------
# Cocotb Variable Export Routine
# ------------------------------------------------------------------------------
# COCOTB_TEST_MODULES and PYGPI_PYTHON_BIN are set by the Edalize `sim` flow
# from `cocotb_module` in aion.core, so they are deliberately not set here.
# COCOTB_TOPLEVEL is: the Icarus runs elaborate two or three root modules and
# cocotb has to be told which one is the DUT.
_setup_cocotb_env: $(UNIVERSAL_LIB)
	$(eval export COCOTB_ANSI_OUTPUT := 1)
	$(eval export COCOTB_TOPLEVEL    := $(TOPLEVEL))
	$(eval export PYTHONPATH         := $(shell realpath $(TEST_DIRS)):$(PYTHONPATH))
