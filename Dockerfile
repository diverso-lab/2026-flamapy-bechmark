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

RUN grep -q 'sat4j.core</artifactId>' fama_src/reasoner_sat4j/pom.xml || sed -i \
      '0,/<\/dependencies>/s|</dependencies>|  <dependency><groupId>org.ow2.sat4j</groupId><artifactId>org.sat4j.core</artifactId><version>2.3</version></dependency>\n  <dependency><groupId>org.ow2.sat4j</groupId><artifactId>org.sat4j.maxsat</artifactId><version>2.3</version></dependency>\n  <dependency><groupId>org.ow2.sat4j</groupId><artifactId>sat4j-maxsat</artifactId><version>2.3</version></dependency>\n  </dependencies>|' \
      fama_src/reasoner_sat4j/pom.xml

# Stub the ANTLR-generated token-type interfaces required by the plain-format
# parser (Analex / FaMaTreeParser). These files were not committed to the repo.
# The plain parser is never used at runtime — only XMLReader is registered.
# NOTE: each RUN can contain at most one heredoc (Docker ends the instruction
#       at the first terminator), so we use two separate RUN steps.
RUN cat > fama_src/FaMaFeatureModel/src/es/us/isa/FAMA/models/FAMAfeatureModel/fileformats/plain/AnalexTokenTypes.java <<'JEOF'
package es.us.isa.FAMA.models.FAMAfeatureModel.fileformats.plain;
public interface AnalexTokenTypes {
    int EOF=1; int NULL_TREE_LOOKAHEAD=3;
    int SALTO=4; int BLANCO=5; int LETRA=6; int BARRA_BAJA=7; int GUION=8;
    int DIGITO=9; int COMILLA=10; int PUNTO=11; int ALMOADILLA=12;
    int LIT_STRING=13; int NUMERO=14; int LIT_ENTERO=15; int MAS=16;
    int COMA=17; int PyC=18; int DOSPUNTOS=19; int PARENTESIS_ABRIR=20;
    int PARENTESIS_CERRAR=21; int LLAVE_ABRIR=22; int LLAVE_CERRAR=23;
    int CORCHETE_ABRIR=24; int CORCHETE_CERRAR=25; int VIRGULA=26;
    int VERSION=27; int COMENT_LIN=28; int IDENT=29;
    int SECCION_RELACIONES=30; int SECCION_CONSTRAINTS=31;
}
JEOF

RUN cat > fama_src/FaMaFeatureModel/src/es/us/isa/FAMA/models/FAMAfeatureModel/fileformats/plain/FaMaTreeParserTokenTypes.java <<'JEOF'
package es.us.isa.FAMA.models.FAMAfeatureModel.fileformats.plain;
public interface FaMaTreeParserTokenTypes {
    int EOF=1; int NULL_TREE_LOOKAHEAD=3;
    int SALTO=4; int BLANCO=5; int LETRA=6; int BARRA_BAJA=7; int GUION=8;
    int DIGITO=9; int COMILLA=10; int PUNTO=11; int ALMOADILLA=12;
    int LIT_STRING=13; int NUMERO=14; int LIT_ENTERO=15; int MAS=16;
    int COMA=17; int PyC=18; int DOSPUNTOS=19; int PARENTESIS_ABRIR=20;
    int PARENTESIS_CERRAR=21; int LLAVE_ABRIR=22; int LLAVE_CERRAR=23;
    int CORCHETE_ABRIR=24; int CORCHETE_CERRAR=25; int VIRGULA=26;
    int VERSION=27; int COMENT_LIN=28; int IDENT=29;
    int SECCION_RELACIONES=30; int SECCION_CONSTRAINTS=31;
    int EXCLUDES=32; int FEATURE_MODEL=33; int CONSTRAINTS=34;
    int CONSTRAINT=35; int FEATURE=36; int DOMINIO=37; int LIT_REAL=38;
    int RELACIONES=39; int RELACION=40; int ATRIBUTO=41; int INTEGER=42;
    int ENUM=43; int DEF_VALUE=44; int NULL_VALUE=45; int VALORES=46;
    int RANGOS=47; int RANGO=48; int CARDINALIDAD=49; int FEATURES=50;
    int INVARIANTES=51; int REQUIRES=52;
}
JEOF

# Build modules in dependency order
RUN cd fama_src/FaMaSDK          && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/FaMaFeatureModel && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/reasoner_choco_2 && mvn install -DskipTests --batch-mode -q
RUN cd fama_src/reasoner_sat4j   && mvn install -DskipTests --batch-mode -q

# Build the CLI fat JAR
COPY fama_cli/ ./fama_cli/
RUN cd fama_cli && mvn package -DskipTests --batch-mode -q


# ---- Stage 2: Python runtime (lean — no JDK or Maven) ----
FROM python:3.11-slim

WORKDIR /benchmark

RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

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
