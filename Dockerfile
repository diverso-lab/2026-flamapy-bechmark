# ---- Stage 1: build the FaMA fat JAR (Maven + JDK, discarded after build) ----
FROM maven:3.9-eclipse-temurin-17 AS builder

WORKDIR /build

# Clone diverso-lab/FaMA — no root pom.xml, so build each module individually
RUN git clone --depth 1 https://github.com/diverso-lab/FaMA fama_src

# Patch any lingering Java-5 source/target declarations
RUN find fama_src -name "pom.xml" \
      -exec sed -i \
          's|<source>5</source>|<source>8</source>|g;s|<target>5</target>|<target>8</target>|g' \
          {} +

# Build in dependency order
RUN cd fama_src/FaMaSDK          && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/FaMaFeatureModel && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/reasoner_choco_2 && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/reasoner_jacop   && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/reasoner_sat4j   && mvn install -DskipTests --batch-mode -q

# Build the thin CLI wrapper fat JAR
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
