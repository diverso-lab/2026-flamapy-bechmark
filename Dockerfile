FROM python:3.11-slim

# System tools: git (to clone FaMA), JDK 17 + Maven (to build it)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        openjdk-17-jdk-headless \
        maven \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /benchmark

# Python deps — cached separately so source changes don't re-install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source
COPY main.py .
COPY scripts/ ./scripts/
COPY fama_cli/ ./fama_cli/
COPY uvlhub_bulk_2026_03_13.zip .

# Build the FaMA JAR at image build time so users don't need Maven at runtime.
# Uses the diverso-lab/FaMA fork (actively maintained, clean multi-module POM).
RUN git clone --depth 1 https://github.com/diverso-lab/FaMA fama_src \
 && find fama_src -name "pom.xml" \
        -exec sed -i \
            's|<source>5</source>|<source>8</source>|g;s|<target>5</target>|<target>8</target>|g' \
            {} + \
 && cd fama_src && mvn install -DskipTests --batch-mode -q \
 && cd /benchmark/fama_cli && mvn package -DskipTests --batch-mode -q \
 && rm -rf /benchmark/fama_src ~/.m2

ENV FAMA_JAR=/benchmark/fama_cli/target/fama-cli-1.0.0-jar-with-dependencies.jar

# Default: run the full benchmark (all solvers, 60 s per-operation timeout).
# Override by passing extra arguments after the image name, e.g.:
#   docker run flamapy-benchmark --max-models 10 --timeout 30 --no-fama
CMD ["python", "main.py", "run", \
     "--zip",      "uvlhub_bulk_2026_03_13.zip", \
     "--fama-jar", "/benchmark/fama_cli/target/fama-cli-1.0.0-jar-with-dependencies.jar", \
     "--output",   "output/benchmark_results.csv", \
     "--timeout",  "60"]
