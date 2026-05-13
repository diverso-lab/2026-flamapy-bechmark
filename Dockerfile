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
# (uses only sed/grep — no python3 required in the builder image)
RUN grep -q 'antlr</artifactId>' fama_src/FaMaFeatureModel/pom.xml || sed -i \
      '0,/<\/dependencies>/s|</dependencies>|  <dependency><groupId>antlr</groupId><artifactId>antlr</artifactId><version>2.7.7</version></dependency>\n  <dependency><groupId>fmapi</groupId><artifactId>Fmapi</artifactId><version>1.0</version></dependency>\n  <dependency><groupId>net.sourceforge.javacsv</groupId><artifactId>javacsv</artifactId><version>1.0</version></dependency>\n  </dependencies>|' \
      fama_src/FaMaFeatureModel/pom.xml

RUN grep -q 'artifactId>choco<' fama_src/reasoner_choco_2/pom.xml || sed -i \
      '0,/<\/dependencies>/s|</dependencies>|  <dependency><groupId>choco</groupId><artifactId>choco</artifactId><version>2.1.0</version></dependency>\n  </dependencies>|' \
      fama_src/reasoner_choco_2/pom.xml

# Fix encoding for reasoner_choco_2 (source files are ISO-8859-1)
RUN grep -q 'ISO-8859-1' fama_src/reasoner_choco_2/pom.xml || sed -i \
      's|<properties>|<properties>\n    <project.build.sourceEncoding>ISO-8859-1</project.build.sourceEncoding>|' \
      fama_src/reasoner_choco_2/pom.xml

RUN grep -q 'artifactId>jacop<' fama_src/reasoner_jacop/pom.xml || sed -i \
      '0,/<\/dependencies>/s|</dependencies>|  <dependency><groupId>org.jacop</groupId><artifactId>jacop</artifactId><version>1.0</version></dependency>\n  <dependency><groupId>jdom</groupId><artifactId>jdom</artifactId><version>1.0</version></dependency>\n  </dependencies>|' \
      fama_src/reasoner_jacop/pom.xml

RUN grep -q 'sat4j.core</artifactId>' fama_src/reasoner_sat4j/pom.xml || sed -i \
      '0,/<\/dependencies>/s|</dependencies>|  <dependency><groupId>org.ow2.sat4j</groupId><artifactId>org.sat4j.core</artifactId><version>2.3</version></dependency>\n  <dependency><groupId>org.ow2.sat4j</groupId><artifactId>org.sat4j.maxsat</artifactId><version>2.3</version></dependency>\n  <dependency><groupId>org.ow2.sat4j</groupId><artifactId>sat4j-maxsat</artifactId><version>2.3</version></dependency>\n  </dependencies>|' \
      fama_src/reasoner_sat4j/pom.xml

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
