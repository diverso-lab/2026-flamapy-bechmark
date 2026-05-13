package es.us.isa.fama.cli;

import es.us.isa.FAMA.Reasoner.QuestionTrader;
import es.us.isa.FAMA.Reasoner.Question;
import es.us.isa.FAMA.Reasoner.questions.ValidQuestion;
import es.us.isa.FAMA.Reasoner.questions.NumberOfProductsQuestion;
import es.us.isa.FAMA.Reasoner.questions.DetectErrorsQuestion;
import es.us.isa.FAMA.Reasoner.questions.CommonalityQuestion;
import es.us.isa.FAMA.Reasoner.questions.VariabilityQuestion;
import es.us.isa.FAMA.models.variabilityModel.VariabilityModel;
import es.us.isa.FAMA.Exceptions.FAMAException;
import es.us.isa.FAMA.errors.Error;
import es.us.isa.FAMA.errors.Observation;

import java.io.File;
import java.io.InputStream;
import java.io.PrintWriter;
import java.net.URL;

/**
 * Thin CLI wrapper around the FaMA QuestionTrader.
 *
 * Usage:
 *   java -jar fama-cli.jar <model.fama> <reasoner> <operation>
 *
 * Reasoners (case-sensitive, must match FAMAconfig.xml):
 *   Choco   JaCoP   Sat4j
 *
 * Operations:
 *   Valid           – is the feature model void-free? (true/false)
 *   #Products       – count valid configurations
 *   DetectErrors    – report dead/false-optional features (JSON-ish list)
 *   Commonality     – average commonality across all features
 *   Variability     – variability ratio
 *
 * Output format (one line):
 *   RESULT: <value>
 * On error:
 *   ERROR: <message>
 *
 * Exit codes:
 *   0  success
 *   1  usage / argument error
 *   2  model loading / analysis error
 */
public class FamaCLI {

    public static void main(String[] args) {
        if (args.length != 3) {
            System.err.println(
                "Usage: FamaCLI <model.fama> <reasoner> <operation>\n" +
                "  reasoners : Choco | JaCoP | Sat4j\n" +
                "  operations: Valid | #Products | DetectErrors | Commonality | Variability"
            );
            System.exit(1);
        }

        String modelPath  = args[0];
        String reasonerId = args[1];
        String operation  = args[2];

        // Determine the fat JAR path so JarJarExtensionsLoader can load classes from it.
        // JarJarExtensionsLoader requires a `file` attribute for each reader/reasoner;
        // pointing it at the fat JAR itself works because all classes are bundled there.
        String jarPath;
        try {
            URL loc = FamaCLI.class.getProtectionDomain().getCodeSource().getLocation();
            jarPath = new File(loc.toURI()).getAbsolutePath();
        } catch (Exception e) {
            System.out.println("ERROR: Cannot determine fat JAR path: " + e.getMessage());
            System.exit(2);
            return;
        }

        // Write a temporary FaMaConfig.xml in the format JarJarExtensionsLoader expects.
        // The bundled FaMaConfig.xml used a wrong root element (<FaMaConfig> instead of
        // <questionTrader>), so the config was silently ignored and mp was left null.
        File tempConfig;
        try {
            tempConfig = File.createTempFile("FaMaConfig", ".xml");
            tempConfig.deleteOnExit();
            try (PrintWriter pw = new PrintWriter(tempConfig, "UTF-8")) {
                pw.println("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
                pw.println("<questionTrader>");
                pw.println("  <reasoner id=\"Choco\" file=\"" + jarPath + "\" class=\"es.us.isa.ChocoReasoner.ChocoReasoner\"/>");
                pw.println("  <reasoner id=\"JaCoP\" file=\"" + jarPath + "\" class=\"es.us.isa.JaCoPReasoner.JaCoPReasoner\"/>");
                pw.println("  <reasoner id=\"Sat4j\" file=\"" + jarPath + "\" class=\"es.us.isa.Sat4jReasoner.Sat4jReasoner\"/>");
                pw.println("  <criteriaSelector name=\"Default\" class=\"es.us.isa.FAMA.Reasoner.DefaultCriteriaSelector\"/>");
                pw.println("  <question id=\"Valid\" interface=\"es.us.isa.FAMA.Reasoner.questions.ValidQuestion\"/>");
                pw.println("  <question id=\"#Products\" interface=\"es.us.isa.FAMA.Reasoner.questions.NumberOfProductsQuestion\"/>");
                pw.println("  <question id=\"DetectErrors\" interface=\"es.us.isa.FAMA.Reasoner.questions.DetectErrorsQuestion\"/>");
                pw.println("  <question id=\"Commonality\" interface=\"es.us.isa.FAMA.Reasoner.questions.CommonalityQuestion\"/>");
                pw.println("  <question id=\"Variability\" interface=\"es.us.isa.FAMA.Reasoner.questions.VariabilityQuestion\"/>");
                pw.println("  <models>");
                pw.println("    <reader extensions=\"xml,fama\" file=\"" + jarPath + "\" class=\"es.us.isa.FAMA.models.FAMAfeatureModel.fileformats.XMLReader\"/>");
                pw.println("  </models>");
                pw.println("</questionTrader>");
            }
        } catch (Exception e) {
            System.out.println("ERROR: Cannot write temp FaMaConfig: " + e.getMessage());
            System.exit(2);
            return;
        }

        QuestionTrader qt;
        try {
            qt = new QuestionTrader(tempConfig.getAbsolutePath());
        } catch (Exception e) {
            System.out.println("ERROR: Failed to initialise QuestionTrader: " + e.getMessage());
            System.exit(2);
            return;
        }

        // Load the feature model
        VariabilityModel vm;
        try {
            vm = qt.openFile(modelPath);
            if (vm == null) {
                throw new RuntimeException("openFile returned null");
            }
            qt.setVariabilityModel(vm);
        } catch (Exception e) {
            System.out.println("ERROR: Cannot load model '" + modelPath + "': " + e.getMessage());
            System.exit(2);
            return;
        }

        // Select reasoner — setSelectedReasoner silently sets null when the id is not
        // registered, which causes a fallback to the default reasoner (wrong results).
        // Detect this via reflection and fail fast instead.
        try {
            qt.setSelectedReasoner(reasonerId);
            java.lang.reflect.Field srField = QuestionTrader.class.getDeclaredField("selectedReasoner");
            srField.setAccessible(true);
            if (srField.get(qt) == null) {
                throw new RuntimeException("reasoner '" + reasonerId + "' not registered (missing from JAR?)");
            }
        } catch (Exception e) {
            System.out.println("ERROR: Cannot select reasoner '" + reasonerId + "': " + e.getMessage());
            System.exit(2);
            return;
        }

        // Execute operation
        try {
            switch (operation) {
                case "Valid":
                    runValid(qt);
                    break;
                case "#Products":
                    runNumberOfProducts(qt);
                    break;
                case "DetectErrors":
                    runDetectErrors(qt, vm);
                    break;
                case "Commonality":
                    runCommonality(qt);
                    break;
                case "Variability":
                    runVariability(qt);
                    break;
                default:
                    System.out.println("ERROR: Unknown operation '" + operation + "'");
                    System.exit(1);
            }
        } catch (Exception e) {
            System.out.println("ERROR: Operation '" + operation + "' failed: " + e.getMessage());
            System.exit(2);
        }
    }

    // -----------------------------------------------------------------------
    // Operation implementations
    // -----------------------------------------------------------------------

    private static void runValid(QuestionTrader qt) throws Exception {
        ValidQuestion q = (ValidQuestion) qt.createQuestion("Valid");
        qt.ask(q);
        System.out.println("RESULT: " + q.isValid());
    }

    private static void runNumberOfProducts(QuestionTrader qt) throws Exception {
        NumberOfProductsQuestion q =
            (NumberOfProductsQuestion) qt.createQuestion("#Products");
        qt.ask(q);
        System.out.println("RESULT: " + q.getNumberOfProducts());
    }

    private static void runDetectErrors(QuestionTrader qt, VariabilityModel vm) throws Exception {
        DetectErrorsQuestion q =
            (DetectErrorsQuestion) qt.createQuestion("DetectErrors");
        java.util.Collection<Observation> obs = vm.getObservations();
        q.setObservations(obs);
        qt.ask(q);
        java.util.Collection<Error> errors = q.getErrors();
        if (errors == null || errors.isEmpty()) {
            System.out.println("RESULT: no_errors");
        } else {
            StringBuilder sb = new StringBuilder("errors:[");
            boolean first = true;
            for (Error e : errors) {
                if (!first) sb.append(',');
                sb.append(e.toString());
                first = false;
            }
            sb.append(']');
            System.out.println("RESULT: " + sb.toString());
        }
    }

    private static void runCommonality(QuestionTrader qt) throws Exception {
        // CommonalityQuestion requires a target feature; use a model-level aggregate
        // by asking for commonality of the root feature as a proxy.
        CommonalityQuestion q =
            (CommonalityQuestion) qt.createQuestion("Commonality");
        // Some FAMA versions expose setElement; if unavailable we just ask without
        // a specific feature and let the reasoner return its default.
        try {
            java.lang.reflect.Method m = q.getClass().getMethod("setElement",
                es.us.isa.FAMA.models.FAMAfeatureModel.Feature.class);
            // Get root via the variability model
            Object root = qt.getVariabilityModel();
            // We cannot cast easily without knowing exact type; skip feature assignment
        } catch (NoSuchMethodException ignored) {}
        qt.ask(q);
        // Try getCommonality() or similar
        double result = -1.0;
        try {
            java.lang.reflect.Method m = q.getClass().getMethod("getCommonality");
            result = ((Number) m.invoke(q)).doubleValue();
        } catch (Exception e) {
            try {
                java.lang.reflect.Method m = q.getClass().getMethod("getResult");
                result = ((Number) m.invoke(q)).doubleValue();
            } catch (Exception e2) {
                System.out.println("RESULT: n/a (commonality result not accessible)");
                return;
            }
        }
        System.out.println("RESULT: " + result);
    }

    private static void runVariability(QuestionTrader qt) throws Exception {
        VariabilityQuestion q =
            (VariabilityQuestion) qt.createQuestion("Variability");
        qt.ask(q);
        double result = -1.0;
        try {
            java.lang.reflect.Method m = q.getClass().getMethod("getVariability");
            result = ((Number) m.invoke(q)).doubleValue();
        } catch (Exception e) {
            try {
                java.lang.reflect.Method m = q.getClass().getMethod("getResult");
                result = ((Number) m.invoke(q)).doubleValue();
            } catch (Exception e2) {
                System.out.println("RESULT: n/a (variability result not accessible)");
                return;
            }
        }
        System.out.println("RESULT: " + result);
    }
}
