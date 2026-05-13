# ---- Stage 1: build the FaMA fat JAR (Maven + JDK, discarded after build) ----
FROM maven:3.9-eclipse-temurin-17 AS builder

WORKDIR /build

RUN git clone --depth 1 https://github.com/diverso-lab/FaMA fama_src

# Patch old Java 5 source/target levels
RUN find fama_src -name "pom.xml" \
      -exec sed -i \
          's|<source>5</source>|<source>8</source>|g;s|<target>5</target>|<target>8</target>|g' \
          {} +

# Install bundled JARs (not in Maven Central) into the local Maven repository
RUN mvn install:install-file -q -Dpackaging=jar \
      -Dfile=fama_src/FaMaFeatureModel/resources/lib/antlr.jar \
      -DgroupId=antlr -DartifactId=antlr -Dversion=2.7.7 \
 && mvn install:install-file -q -Dpackaging=jar \
      -Dfile=fama_src/FaMaFeatureModel/resources/lib/javacsv.jar \
      -DgroupId=net.sourceforge.javacsv -DartifactId=javacsv -Dversion=1.0 \
 && mvn install:install-file -q -Dpackaging=jar \
      -Dfile=fama_src/FaMaFeatureModel/resources/lib/Fmapi.jar \
      -DgroupId=fmapi -DartifactId=Fmapi -Dversion=1.0 \
 && mvn install:install-file -q -Dpackaging=jar \
      -Dfile=fama_src/reasoner_choco_2/resources/lib/choco-2.1.0-basic+old.jar \
      -DgroupId=choco -DartifactId=choco -Dversion=2.1.0 \
 && mvn install:install-file -q -Dpackaging=jar \
      -Dfile=fama_src/reasoner_jacop/resources/lib/JaCoP.jar \
      -DgroupId=org.jacop -DartifactId=jacop -Dversion=1.0 \
 && mvn install:install-file -q -Dpackaging=jar \
      -Dfile=fama_src/reasoner_jacop/resources/lib/jdom.jar \
      -DgroupId=jdom -DartifactId=jdom -Dversion=1.0 \
 && mvn install:install-file -q -Dpackaging=jar \
      -Dfile=fama_src/reasoner_sat4j/resources/lib/org.sat4j.core.jar \
      -DgroupId=org.ow2.sat4j -DartifactId=org.sat4j.core -Dversion=2.3 \
 && mvn install:install-file -q -Dpackaging=jar \
      -Dfile=fama_src/reasoner_sat4j/resources/lib/org.sat4j.maxsat.jar \
      -DgroupId=org.ow2.sat4j -DartifactId=org.sat4j.maxsat -Dversion=2.3 \
 && mvn install:install-file -q -Dpackaging=jar \
      -Dfile=fama_src/reasoner_sat4j/resources/lib/sat4j-maxsat.jar \
      -DgroupId=org.ow2.sat4j -DartifactId=sat4j-maxsat -Dversion=2.3

# Patch each module's pom.xml to declare the bundled JARs as compile dependencies
RUN python3 - <<'PYEOF'
import re, os

SRC = "fama_src"

def patch(path, marker, deps):
    pom = open(path).read()
    if marker in pom:
        return  # already patched
    pom = pom.replace("</dependencies>", deps + "\n  </dependencies>", 1)
    open(path, "w").write(pom)

patch(f"{SRC}/FaMaFeatureModel/pom.xml", "antlr</artifactId>", """
    <dependency><groupId>antlr</groupId><artifactId>antlr</artifactId><version>2.7.7</version></dependency>
    <dependency><groupId>fmapi</groupId><artifactId>Fmapi</artifactId><version>1.0</version></dependency>
    <dependency><groupId>net.sourceforge.javacsv</groupId><artifactId>javacsv</artifactId><version>1.0</version></dependency>""")

patch(f"{SRC}/reasoner_choco_2/pom.xml", "artifactId>choco<", """
    <dependency><groupId>choco</groupId><artifactId>choco</artifactId><version>2.1.0</version></dependency>""")

# Also fix encoding for reasoner_choco_2 (source files are ISO-8859-1)
choco_pom = f"{SRC}/reasoner_choco_2/pom.xml"
pom = open(choco_pom).read()
if "ISO-8859-1" not in pom and "<properties>" in pom:
    pom = pom.replace("<properties>", "<properties>\n    <project.build.sourceEncoding>ISO-8859-1</project.build.sourceEncoding>", 1)
    open(choco_pom, "w").write(pom)

patch(f"{SRC}/reasoner_jacop/pom.xml", "artifactId>jacop<", """
    <dependency><groupId>org.jacop</groupId><artifactId>jacop</artifactId><version>1.0</version></dependency>
    <dependency><groupId>jdom</groupId><artifactId>jdom</artifactId><version>1.0</version></dependency>""")

patch(f"{SRC}/reasoner_sat4j/pom.xml", "sat4j.core</artifactId>", """
    <dependency><groupId>org.ow2.sat4j</groupId><artifactId>org.sat4j.core</artifactId><version>2.3</version></dependency>
    <dependency><groupId>org.ow2.sat4j</groupId><artifactId>org.sat4j.maxsat</artifactId><version>2.3</version></dependency>
    <dependency><groupId>org.ow2.sat4j</groupId><artifactId>sat4j-maxsat</artifactId><version>2.3</version></dependency>""")
PYEOF

# Build modules in dependency order
RUN cd fama_src/FaMaSDK          && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/FaMaFeatureModel && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/reasoner_choco_2 && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/reasoner_jacop   && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/reasoner_sat4j   && mvn install -DskipTests --batch-mode -q

# Build the CLI fat JAR
COPY fama_cli/ ./fama_cli/
RUN cd fama_cli && mvn package -DskipTests --batch-mode -q


# ---- Stage 2: Python runtime (lean — no JDK or Maven) ----
FROM python:3.11-slim

WORKDIR /benchmark

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY scripts/ ./scripts/
COPY uvlhub_bulk_2026_03_13.zip .

# Copy only the JAR from the builder stage
COPY --from=builder /build/fama_cli/target/fama-cli-1.0.0-jar-with-dependencies.jar \
     ./fama_cli/fama-cli.jar

# Default: run the full benchmark (all solvers, 60 s per-operation timeout).
# Override by passing extra arguments after the image name, e.g.:
#   docker run flamapy-benchmark python main.py run --max-models 10 --no-fama
CMD ["python", "main.py", "run", \
     "--zip",      "uvlhub_bulk_2026_03_13.zip", \
     "--fama-jar", "fama_cli/fama-cli.jar", \
     "--output",   "output/benchmark_results.csv", \
     "--timeout",  "60"]
