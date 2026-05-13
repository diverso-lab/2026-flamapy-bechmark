#!/usr/bin/env bash
# =============================================================================
# run_benchmark.sh – build everything and run the full solver benchmark
#
# This script is self-contained: it installs Python dependencies, builds the
# FaMA CLI JAR (requires JDK + Maven), and then runs the benchmark with ALL
# solvers (PySAT × 6, BDD, Z3, FaMA/Choco, FaMA/JaCoP, FaMA/Sat4j) using
# a 900 s per-operation timeout and 4 parallel workers.
#
# Usage:
#   bash run_benchmark.sh <models.zip>
#
# Environment variables (all optional):
#   FAMA_REPO   Git URL to clone FaMA from
#               (default: https://github.com/diverso-lab/FaMA)
#   SKIP_FAMA   Set to 1 to skip the FaMA build and benchmark entirely
#               (useful when Java/Maven are unavailable)
#   PYTHON      Python interpreter to use (default: python3)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAMA_SRC_DIR="${SCRIPT_DIR}/fama_src"
CLI_DIR="${SCRIPT_DIR}/fama_cli"
FAT_JAR="${CLI_DIR}/target/fama-cli-1.0.0-jar-with-dependencies.jar"
FAMA_REPO="${FAMA_REPO:-https://github.com/jagalindo/fama}"
SKIP_FAMA="${SKIP_FAMA:-0}"
PYTHON="${PYTHON:-python3}"

# ---------------------------------------------------------------------------
info()    { echo "[INFO]  $*"; }
warning() { echo "[WARN]  $*"; }
error()   { echo "[ERROR] $*" >&2; exit 1; }
step()    { echo; echo "==> $*"; }

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
if [ $# -ne 1 ]; then
    echo "Usage: $0 <models.zip>" >&2
    exit 1
fi

ZIP_FILE="$1"

[ -f "${ZIP_FILE}" ] || error "ZIP file not found: ${ZIP_FILE}"

# ---------------------------------------------------------------------------
# Step 1 – Python dependencies
# ---------------------------------------------------------------------------
step "Installing Python dependencies …"
"${PYTHON}" -m pip install --quiet --upgrade pip
"${PYTHON}" -m pip install --quiet -r "${SCRIPT_DIR}/requirements.txt"
info "Python dependencies OK."

# ---------------------------------------------------------------------------
# Step 2 – FaMA build (skipped if SKIP_FAMA=1 or toolchain unavailable)
# ---------------------------------------------------------------------------
FAMA_JAR_ARG=""

if [ "${SKIP_FAMA}" = "1" ]; then
    warning "SKIP_FAMA=1 – skipping FaMA build and benchmark."
else
    HAS_JAVA=0; HAS_MVN=0
    command -v java >/dev/null 2>&1 && HAS_JAVA=1
    command -v mvn  >/dev/null 2>&1 && HAS_MVN=1

    if [ "${HAS_JAVA}" = "0" ] || [ "${HAS_MVN}" = "0" ]; then
        error "java and mvn are required. Install JDK 11+ and Maven 3.6+ and re-run."
    else
        step "Building FaMA …"

        # 2a – clone FaMA source (skip if already present)
        if [ -d "${FAMA_SRC_DIR}/.git" ]; then
            info "FaMA source already at ${FAMA_SRC_DIR}; skipping clone."
        else
            info "Cloning ${FAMA_REPO} …"
            git clone "${FAMA_REPO}" "${FAMA_SRC_DIR}"
        fi

        # 2b – patch Java source/target versions (old POMs use Java 5 which is
        #       unsupported by JDK 11+; upgrade to 8 in-place before building)
        info "Patching FaMA POMs: Java 5 → 8 …"
        find "${FAMA_SRC_DIR}" -name "pom.xml" -exec \
            sed -i 's|<source>5</source>|<source>8</source>|g;
                    s|<target>5</target>|<target>8</target>|g' {} +

        # 2c – install ALL bundled JARs from modules' resources/lib/ dirs into
        #       the local Maven repo so they are resolvable as compile deps.
        #       FAMAAttributedFeatureModel is intentionally omitted (not needed).
        info "Installing bundled JARs into local Maven repo …"
        _install_jar() {
            local file="$1" gid="$2" aid="$3" ver="$4"
            [ -f "$file" ] && mvn install:install-file -Dfile="$file" \
                -DgroupId="$gid" -DartifactId="$aid" -Dversion="$ver" \
                -Dpackaging=jar --batch-mode -q \
                && info "    Installed $aid-$ver" || warning "    Not found: $file"
        }
        _install_jar "${FAMA_SRC_DIR}/FaMaFeatureModel/resources/lib/antlr.jar" \
            antlr antlr 2.7.7
        _install_jar "${FAMA_SRC_DIR}/FaMaFeatureModel/resources/lib/Fmapi.jar" \
            fmapi Fmapi 1.0
        _install_jar "${FAMA_SRC_DIR}/FaMaFeatureModel/resources/lib/javacsv.jar" \
            net.sourceforge.javacsv javacsv 1.0
        _install_jar "${FAMA_SRC_DIR}/reasoner_choco_2/resources/lib/choco-2.1.0-basic+old.jar" \
            choco choco 2.1.0
        _install_jar "${FAMA_SRC_DIR}/reasoner_jacop/resources/lib/JaCoP.jar" \
            org.jacop jacop 1.0
        _install_jar "${FAMA_SRC_DIR}/reasoner_jacop/resources/lib/jdom.jar" \
            jdom jdom 1.0
        _install_jar "${FAMA_SRC_DIR}/reasoner_java_bdd/resources/lib/javabdd-1.0b2.jar" \
            net.sf.javabdd javabdd 1.0b2
        _install_jar "${FAMA_SRC_DIR}/reasoner_sat4j/resources/lib/org.sat4j.core.jar" \
            org.ow2.sat4j org.sat4j.core 2.3
        _install_jar "${FAMA_SRC_DIR}/reasoner_sat4j/resources/lib/org.sat4j.maxsat.jar" \
            org.ow2.sat4j org.sat4j.maxsat 2.3
        _install_jar "${FAMA_SRC_DIR}/reasoner_sat4j/resources/lib/sat4j-maxsat.jar" \
            org.ow2.sat4j sat4j-maxsat 2.3

        # 2d – patch each module's pom.xml to declare the bundled JARs as deps
        info "Patching module pom.xml files with bundled JAR dependencies …"
        FAMA_SRC_DIR_PY="${FAMA_SRC_DIR}" "${PYTHON}" - <<'PYEOF'
import os

SRC = os.environ["FAMA_SRC_DIR_PY"]

def patch_pom(rel_path, marker, extra_deps):
    path = SRC + rel_path
    pom = open(path).read()
    if marker in pom:
        print(f"[INFO]    {rel_path}: already patched, skipping.")
        return
    pom = pom.replace("</dependencies>", extra_deps + "  </dependencies>", 1)
    open(path, "w").write(pom)
    print(f"[INFO]    {rel_path}: patched.")

patch_pom("/FaMaFeatureModel/pom.xml", "antlr</artifactId>", """
    <dependency><groupId>antlr</groupId><artifactId>antlr</artifactId><version>2.7.7</version></dependency>
    <dependency><groupId>fmapi</groupId><artifactId>Fmapi</artifactId><version>1.0</version></dependency>
    <dependency><groupId>net.sourceforge.javacsv</groupId><artifactId>javacsv</artifactId><version>1.0</version></dependency>
""")
patch_pom("/reasoner_choco_2/pom.xml", "<artifactId>choco</artifactId>", """
    <dependency><groupId>choco</groupId><artifactId>choco</artifactId><version>2.1.0</version></dependency>
""")
# Also fix encoding for reasoner_choco_2 (source files are ISO-8859-1)
choco2_pom = SRC + "/reasoner_choco_2/pom.xml"
pom = open(choco2_pom).read()
if "ISO-8859-1" not in pom and "<properties>" in pom:
    pom = pom.replace("<properties>", "<properties>\n    <project.build.sourceEncoding>ISO-8859-1</project.build.sourceEncoding>", 1)
    open(choco2_pom, "w").write(pom)
    print("[INFO]    /reasoner_choco_2/pom.xml: patched encoding.")
patch_pom("/reasoner_jacop/pom.xml", "jacop</artifactId>", """
    <dependency><groupId>org.jacop</groupId><artifactId>jacop</artifactId><version>1.0</version></dependency>
    <dependency><groupId>jdom</groupId><artifactId>jdom</artifactId><version>1.0</version></dependency>
""")
patch_pom("/reasoner_java_bdd/pom.xml", "javabdd</artifactId>", """
    <dependency><groupId>net.sf.javabdd</groupId><artifactId>javabdd</artifactId><version>1.0b2</version></dependency>
""")
patch_pom("/reasoner_sat4j/pom.xml", "sat4j.core</artifactId>", """
    <dependency><groupId>org.ow2.sat4j</groupId><artifactId>org.sat4j.core</artifactId><version>2.3</version></dependency>
    <dependency><groupId>org.ow2.sat4j</groupId><artifactId>org.sat4j.maxsat</artifactId><version>2.3</version></dependency>
    <dependency><groupId>org.ow2.sat4j</groupId><artifactId>sat4j-maxsat</artifactId><version>2.3</version></dependency>
""")
PYEOF

        # install FaMA modules into local Maven repository (no root POM,
        #       so each module is built individually in dependency order;
        #       FAMAAttributedFeatureModel is omitted – not needed)
        info "Installing FaMA modules into local Maven repo (this may take a few minutes) …"
        for MODULE in FaMaSDK FaMaFeatureModel \
                      reasoner_choco_2 reasoner_jacop \
                      reasoner_java_bdd reasoner_sat4j; do
            MODULE_DIR="${FAMA_SRC_DIR}/${MODULE}"
            if [ -d "${MODULE_DIR}" ]; then
                info "  Installing ${MODULE} …"
                cd "${MODULE_DIR}"
                mvn install -DskipTests --batch-mode -q
                cd "${SCRIPT_DIR}"
            else
                warning "  Module directory not found, skipping: ${MODULE_DIR}"
            fi
        done
        info "FaMA modules installed."

        # 2c – build the CLI wrapper fat JAR
        info "Building FamaCLI fat JAR …"
        cd "${CLI_DIR}"
        mvn package -DskipTests --batch-mode -q
        cd "${SCRIPT_DIR}"

        if [ ! -f "${FAT_JAR}" ]; then
            warning "Maven finished but JAR not found at ${FAT_JAR}; skipping FaMA."
        else
            info "FamaCLI JAR ready: ${FAT_JAR}"
            FAMA_JAR_ARG="--fama-jar ${FAT_JAR}"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Step 3 – Run the benchmark
# ---------------------------------------------------------------------------
step "Starting benchmark …"
info "ZIP      : ${ZIP_FILE}"
info "Timeout  : 900 s per operation"
info "Workers  : 6"
[ -n "${FAMA_JAR_ARG}" ] && info "FaMA JAR : ${FAT_JAR}" || info "FaMA     : disabled"
echo

# shellcheck disable=SC2086
"${PYTHON}" "${SCRIPT_DIR}/main.py" run \
    --zip     "${ZIP_FILE}"              \
    --timeout 900                        \
    --workers 6                          \
    ${FAMA_JAR_ARG}
