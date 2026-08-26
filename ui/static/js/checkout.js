// ============================================================
//  Propositions de sortie (checkout)
//
//  Les routes sont toujours données en DOUBLE OUT, même dans une
//  partie single out : une séquence qui tombe pile à zéro termine la
//  partie dans les deux cas, la proposition n'est donc jamais fausse —
//  seulement plus prudente que nécessaire en single out.
//
//  De 41 à 170 avec trois flèches en main, on utilise la table
//  canonique : ce sont les routes que les joueurs connaissent par cœur
//  et qu'un solveur ne retrouverait pas toujours (137 se sort en
//  T17-T18-D16, pas en T20-T19-D10).
//
//  En dessous, et dès qu'il reste moins de trois flèches, les routes
//  sont calculées : la table ne couvre pas ces cas.
// ============================================================

// Table à trois flèches, 41 à 170. Les scores absents (169, 168, 166,
// 165, 163, 162, 159) n'ont aucune sortie possible en double out.
const CHECKOUT_3 = {
  170: ["T20","T20","DB"], 167: ["T20","T19","DB"], 164: ["T20","T18","DB"],
  161: ["T20","T17","DB"], 160: ["T20","T20","D20"], 158: ["T20","T20","D19"],
  157: ["T20","T19","D20"], 156: ["T20","T20","D18"], 155: ["T20","T15","DB"],
  154: ["T20","T18","D20"], 153: ["T20","T19","D18"], 152: ["T20","T20","D16"],
  151: ["T20","T17","D20"], 150: ["T20","T18","D18"], 149: ["T20","T19","D16"],
  148: ["T20","T16","D20"], 147: ["T20","T17","D18"], 146: ["T20","T18","D16"],
  145: ["T20","T15","D20"], 144: ["T20","T20","D12"], 143: ["T20","T17","D16"],
  142: ["T20","T14","D20"], 141: ["T20","T15","D18"], 140: ["T20","T16","D16"],
  139: ["T20","T13","D20"], 138: ["T20","T14","D18"], 137: ["T17","T18","D16"],
  136: ["T20","T20","D8"],  135: ["T20","T15","D15"], 134: ["T20","T14","D16"],
  133: ["T20","T19","D8"],  132: ["T20","T20","D6"],  131: ["T20","T13","D16"],
  130: ["T20","T18","D8"],  129: ["T19","T20","D6"],  128: ["T18","T14","D16"],
  127: ["T20","T17","D8"],  126: ["T19","T19","D6"],  125: ["25","T20","D20"],
  124: ["T20","T16","D8"],  123: ["T19","T16","D9"],  122: ["T18","T20","D4"],
  121: ["T20","T15","D8"],  120: ["T20","20","D20"],  119: ["T19","T10","D16"],
  118: ["T20","18","D20"],  117: ["T20","17","D20"],  116: ["T20","16","D20"],
  115: ["T20","15","D20"],  114: ["T20","14","D20"],  113: ["T20","13","D20"],
  112: ["T20","20","D16"],  111: ["T20","19","D16"],  110: ["T20","18","D16"],
  109: ["T20","17","D16"],  108: ["T20","16","D16"],  107: ["T19","18","D16"],
  106: ["T20","14","D16"],  105: ["T20","13","D16"],  104: ["T18","18","D16"],
  103: ["T20","11","D16"],  102: ["T20","10","D16"],  101: ["T17","18","D16"],
  100: ["T20","D20"],       99:  ["T19","10","D16"],  98:  ["T20","D19"],
  97:  ["T19","D20"],       96:  ["T20","D18"],       95:  ["T15","18","D16"],
  94:  ["T18","D20"],       93:  ["T19","D18"],       92:  ["T20","D16"],
  91:  ["T17","D20"],       90:  ["T18","D18"],       89:  ["T19","D16"],
  88:  ["T16","D20"],       87:  ["T17","D18"],       86:  ["T18","D16"],
  85:  ["T15","D20"],       84:  ["T20","D12"],       83:  ["T17","D16"],
  82:  ["T14","D20"],       81:  ["T15","D18"],       80:  ["T16","D16"],
  79:  ["T13","D20"],       78:  ["T14","D18"],       77:  ["T15","D16"],
  76:  ["T20","D8"],        75:  ["T13","D18"],       74:  ["T14","D16"],
  73:  ["T19","D8"],        72:  ["T20","D6"],        71:  ["T13","D16"],
  70:  ["T20","D5"],        69:  ["T19","D6"],        68:  ["T16","D10"],
  67:  ["T17","D8"],        66:  ["T10","D18"],       65:  ["T15","D10"],
  64:  ["T16","D8"],        63:  ["T13","D12"],       62:  ["T10","D16"],
  61:  ["T15","D8"],        60:  ["20","D20"],        59:  ["19","D20"],
  58:  ["18","D20"],        57:  ["17","D20"],        56:  ["16","D20"],
  55:  ["15","D20"],        54:  ["14","D20"],        53:  ["13","D20"],
  52:  ["20","D16"],        51:  ["19","D16"],        50:  ["18","D16"],
  49:  ["17","D16"],        48:  ["16","D16"],        47:  ["15","D16"],
  46:  ["14","D16"],        45:  ["13","D16"],        44:  ["12","D16"],
  43:  ["11","D16"],        42:  ["10","D16"],        41:  ["9","D16"],
};

// Doubles par ordre de préférence pour terminer. D20 et D16 d'abord :
// ce sont les plus larges à viser, et rater D16 laisse D8 puis D4,
// une suite de rattrapage propre. DB en dernier, c'est la plus petite.
const PREFERRED_DOUBLES = [
  "D20","D16","D18","D12","D10","D8","D14","D6","D4","D2",
  "D19","D17","D15","D13","D11","D9","D7","D5","D3","D1","DB",
];

function dartValue(label) {
  if (label === "DB") return 50;
  if (label === "25") return 25;
  if (label[0] === "T") return 3 * parseInt(label.slice(1), 10);
  if (label[0] === "D") return 2 * parseInt(label.slice(1), 10);
  return parseInt(label, 10);
}

// Étiquette d'une flèche valant n, la plus sûre à viser d'abord :
// un simple large avant un triple, un triple avant un double étroit.
function dartFor(n) {
  if (n >= 1 && n <= 20) return String(n);
  if (n === 25) return "25";
  if (n === 50) return "DB";
  if (n % 3 === 0 && n / 3 <= 20) return "T" + (n / 3);
  if (n % 2 === 0 && n / 2 <= 20) return "D" + (n / 2);
  return null;
}

function finishOneDart(score) {
  if (score === 50) return ["DB"];
  if (score <= 40 && score >= 2 && score % 2 === 0) return ["D" + (score / 2)];
  return null;
}

function finishTwoDarts(score) {
  for (const dbl of PREFERRED_DOUBLES) {
    const first = score - dartValue(dbl);
    if (first < 1) continue;
    const label = dartFor(first);
    if (label) return [label, dbl];
  }
  return null;
}

/**
 * Route conseillée, ou null s'il n'y en a aucune.
 * score      : points restants
 * dartsLeft  : flèches encore en main dans le tour (1 à 3)
 */
function checkoutRoute(score, dartsLeft) {
  if (!(score > 1)) return null;

  // Une flèche suffit : toujours le mieux, quel que soit le reste en main.
  const one = finishOneDart(score);
  if (one) return one;

  // La route de l'affiche est prioritaire dès qu'elle tient dans les
  // flèches restantes. Beaucoup de ses entrées ne font que deux flèches
  // (100 = T20-D20), elles restent donc valables en fin de tour.
  const canon = CHECKOUT_3[score];
  if (canon && canon.length <= dartsLeft) return canon;

  // Sinon on calcule : soit le score est sous 41 et absent de la table,
  // soit sa route canonique demande plus de flèches qu'il n'en reste.
  if (dartsLeft < 2) return null;
  return finishTwoDarts(score);
}
