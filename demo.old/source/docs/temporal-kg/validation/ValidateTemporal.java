import java.io.File;
import java.util.*;
import org.semanticweb.owlapi.apibinding.OWLManager;
import org.semanticweb.owlapi.model.*;
import org.semanticweb.owlapi.profiles.OWL2DLProfile;
import org.semanticweb.owlapi.reasoner.OWLReasoner;
import org.semanticweb.HermiT.ReasonerFactory;

public class ValidateTemporal {
  static final String NS="https://example.org/temporal-kg/v2#";
  static OWLDataFactory d=OWLManager.getOWLDataFactory();
  static OWLClass c(String s){return d.getOWLClass(IRI.create(NS+s));}
  static OWLObjectProperty p(String s){return d.getOWLObjectProperty(IRI.create(NS+s));}
  static OWLNamedIndividual n(String s){return d.getOWLNamedIndividual(IRI.create(NS+s));}
  static OWLAxiom type(String x,String cls){return d.getOWLClassAssertionAxiom(c(cls),n(x));}
  static OWLAxiom edge(String prop,String x,String y){return d.getOWLObjectPropertyAssertionAxiom(p(prop),n(x),n(y));}
  static void check(String name, boolean ok){if(!ok)throw new AssertionError(name);System.out.println("PASS: "+name);}
  static OWLOntology load(String file, OWLAxiom...extra)throws Exception{
    OWLOntologyManager m=OWLManager.createOWLOntologyManager();
    OWLOntology o=m.loadOntologyFromOntologyDocument(new File(file));
    m.addAxioms(o,new HashSet<>(Arrays.asList(extra)));return o;
  }
  static void consistency(String name,String file,boolean expected, OWLAxiom...extra)throws Exception{
    OWLOntology o=load(file,extra); OWLReasoner r=new ReasonerFactory().createReasoner(o);
    try{check(name,r.isConsistent()==expected);}finally{r.dispose();}
  }
  public static void main(String[] args)throws Exception{
    String core=args[0], example=args[1];
    for(String file:args){OWLOntology o=load(file);check("OWL 2 DL profile: "+file,new OWL2DLProfile().checkOntology(o).isInProfile());}
    OWLOntology o=load(example);OWLReasoner r=new ReasonerFactory().createReasoner(o);
    check("example consistent",r.isConsistent());
    check("all named classes satisfiable",r.getUnsatisfiableClasses().getEntitiesMinusBottom().isEmpty());
    check("antibiotic administration inferred",r.isEntailed(type("administration1","AntibioticAdministration")));
    check("PRO chain infers bearer participation",r.isEntailed(edge("hasParticipant","administration1","patient1")));
    check("interval occurrence inferred as ExtendedProcess",r.isEntailed(type("administration1","ExtendedProcess")));
    r.dispose();
    consistency("point and interval on same individual inconsistent",core,false,type("x","TimePoint"),type("x","TimeInterval"));
    consistency("identical beginning and end inconsistent",core,false,edge("hasBeginning","i","p"),edge("hasEnd","i","p"));
    consistency("interval with unnamed endpoints consistent",core,true,type("i","TimeInterval"));
    consistency("process with unknown extent consistent",core,true,type("event","Process"));
    consistency("point occurrence consistent",core,true,type("p","TimePoint"),edge("exactExtent","event","p"));
    consistency("point and interval exact extents on one process inconsistent",core,false,type("p","TimePoint"),type("i","TimeInterval"),edge("exactExtent","event","p"),edge("exactExtent","event","i"));
    consistency("two-way pointBefore inconsistent",core,false,edge("pointBefore","p","q"),edge("pointBefore","q","p"));
    o=load(core,edge("hasBeginning","i","p"),edge("hasBeginning","i","q"));
    r=new ReasonerFactory().createReasoner(o);
    check("functional endpoint infers equality",r.isEntailed(d.getOWLSameIndividualAxiom(n("p"),n("q"))));r.dispose();
    consistency("different functional endpoint fillers inconsistent",core,false,edge("hasBeginning","i","p"),edge("hasBeginning","i","q"),d.getOWLDifferentIndividualsAxiom(n("p"),n("q")));
    o=load(core,edge("pointBefore","p","q"),edge("pointBefore","q","r"));r=new ReasonerFactory().createReasoner(o);
    check("pointBefore closure not entailed by core",!r.isEntailed(edge("pointBefore","p","r")));r.dispose();
    consistency("reversed interval endpoint order awaits external validation",core,true,edge("hasBeginning","i","start"),edge("hasEnd","i","end"),edge("pointBefore","end","start"));
    System.out.println("All checks passed. These validate the OWL core, not a temporal query engine.");
  }
}
