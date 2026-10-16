"""
e0_direct_llm_finqa_autogen.py

VERSAO 2 (apos a analise dos resultados do E2) -- mudancas que valem para
TODOS os experimentos E0..E7 (rode de novo E0 e E1 para manter a comparacao):
    - HISTORY: o historico da conversa (perguntas/respostas anteriores do
      ConvFinQA) agora vai no bloco HISTORY. Antes ia misturado no TEXT e
      HISTORY aparecia como "(none)" em 64 das 100 perguntas.
    - Leitura de JSON: pega o ULTIMO objeto JSON completo da resposta (antes
      juntava varios blocos num JSON invalido).
    - Nova metrica EM_escala_percentual: no ConvFinQA os percentuais estao
      como fracao (36.93% = 0.3693). Em perguntas de percentual/razao ela
      aceita tambem gold x100 ou gold/100. Use-a para comparar E0..E7; o EM
      estrito continua sendo reportado. O CSV ganhou as colunas question,
      is_percent_question e correct_escala_pct.


EXPERIMENTO E0 - LLM DIRETO (BASELINE) da ablacao.

Pergunta cientifica: quanto o LLM resolve sozinho?

Versao LOCAL (via Ollama) com UMA UNICA chamada ao LLM por pergunta.
Nenhum critico, nenhum PoT, nenhuma calculadora.

Fluxo (por pergunta):

    Contexto (Question + Text + Table + History)
        |
        v
       LLM   (system: 1a frase do prompt do especialista do script de
        |      referencia; user: prompt E0 do documento de ablacao)
        v
    Resposta

    Apenas no final (fora do loop) o gabarito e usado para contabilizar
    acertos. Esse e o ponto de referencia: todos os demais experimentos
    (E1..E7) precisam demonstrar alguma vantagem em relacao a ele.

Mantidos constantes em relacao ao script de referencia
(AgenteCritico_1Especialista_1Critico_METRICAS_ReasoningON_Amostra_CORRIGIDO):
    - AutoGen (pyautogen==0.2.35), modelo (llama3:8b), temperatura (0.2),
      CSV/amostra, MAX_ITEMS
    - criterio de acerto (_answers_match, tol = 1e-2)

Prompt:
    - do script de referencia fica APENAS a frase de papel/persona
      ("You are a financial analysis agent ..."). Manter a MESMA frase
      como system prompt em E1..E7;
    - foram retiradas as instrucoes de extrair dados, calcular, explicar
      passo a passo e o JSON com "steps" (pertencem ao E1);
    - como o prompt E0 pede "Return only the final answer" (sem JSON), a
      resposta e lida por extract_direct_answer(). O modo de extracao
      usado e registrado por linha (answer_extraction).

Metricas calculadas (somente as que fazem sentido para uma resposta direta):
    - Answer-level: EM (Exact Match / Accuracy), MAE, erro relativo medio
    - MSE / RMSE
    - Intervalo de Confianca de Wilson (95%) para o EM
    - Unit accuracy heuristica
    - Operacionais: tokens, custo hipotetico, latencia, chamadas ao LLM,
      respostas sem numero extraivel

Removido por NAO fazer sentido no E0:
    - Metricas do critico (Correction/Damage Rate, Judge Precision/Recall,
      ganho especialista -> critico) e colunas sem/com critico: nao ha
      critico.
    - Qui-Quadrado (critico chamado x resposta correta): degenerado sem
      critico.
    - Kappa de Cohen (sem vs com critico): trivialmente 1.0 sem critico.
    - Avaliacao reasoning-level por LLM (Extraction/Plan/Execution
      Accuracy): o E0 devolve apenas a resposta final, sem raciocinio
      visivel, entao nao ha extracao nem plano a avaliar.

Comparacoes entre experimentos (Kappa, McNemar) serao feitas depois, num
script separado, entre os CSVs de E0..E7 pareados por question_id, usando
a coluna 'correct' de cada um.

Saidas:
    - OUT_PATH                        -> resultados por pergunta (CSV)
    - *_metricas.csv                  -> metricas agregadas (1 linha)
    - *_per_example.jsonl             -> {"id","gold","prediction","correct",
                                          "latency","llm_calls"} por exemplo

Como rodar localmente:
    1. Instale e rode o Ollama: https://ollama.com
    2. Baixe o modelo:            ollama pull llama3:8b
    3. Confirme que o servidor esta ativo em http://localhost:11434
    4. Instale as dependencias:   pip install "pyautogen==0.2.35" pandas
       (IMPORTANTE: a partir da versao 0.4/pyautogen>=0.9 o pacote virou um
       proxy para a nova arquitetura assincrona "autogen-agentchat". Este
       script usa a API classica sincrona, por isso a versao fixada 0.2.35.)
    5. Ajuste as variaveis de configuracao abaixo se necessario
    6. Rode:                      python e0_direct_llm_finqa_autogen.py

Requisitos:
    pip install "pyautogen==0.2.35" pandas
    (opcional) pip install scipy   -> z exato para o Intervalo de Confianca
"""

import json
import math
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

try:
    import autogen
    from autogen import AssistantAgent
except ImportError:
    print("Instale o SDK primeiro: pip install \"pyautogen==0.2.35\"", file=sys.stderr)
    raise

# scipy e usado APENAS para o valor "z" exato do Intervalo de Confianca.
# E opcional: se nao estiver instalado, o script usa valores de z
# tabelados (90/95/99%).
try:
    from scipy import stats as scipy_stats
    SCIPY_AVAILABLE = True
except ImportError:
    scipy_stats = None
    SCIPY_AVAILABLE = False


# ---------------------------------------------------------------------------
# CONFIGURACAO - ajuste aqui
# ---------------------------------------------------------------------------

EXPERIMENT_ID = "E0_direct"
CSV_PATH = "convfinqa_amostra_100.csv"
OLLAMA_MODEL = "llama3:8b"
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_API_KEY = "ollama"          # Ollama ignora o valor, mas o SDK exige algum
MAX_ITEMS = 100                    # quantas linhas do CSV serao lidas/processadas
TIMEOUT_SECONDS = 600              # timeout por chamada ao modelo
OUT_PATH = "results_E0_direct_autogen_v2.csv"
PER_EXAMPLE_JSONL_PATH = OUT_PATH.replace(".csv", "_per_example.jsonl")
VERBOSE = True

# Mesma temperatura do especialista do script de referencia.
DIRECT_TEMPERATURE = 0.2

# Colunas do CSV. O script de referencia usa apenas "text" (contexto +
# pergunta juntos) e "output" (gabarito). Se o seu CSV tiver colunas
# separadas para pergunta / tabela / historico (ex.: ConvFinQA), informe os
# nomes aqui para preencher cada bloco do prompt E0. Colunas inexistentes
# sao ignoradas automaticamente (fallback descrito em build_fields).
TEXT_COL = "text"
GOLD_COL = "output"
QUESTION_COL = "question"          # opcional
TABLE_COL = "table"                # opcional
HISTORY_COL = "history"            # opcional (ConvFinQA)

# Preco de referencia (USD por 1 milhao de tokens) usado SOMENTE para simular
# quanto custaria caso essa mesma carga de tokens fosse rodada em uma API
# paga. A execucao real deste script e local/gratuita via Ollama.
REFERENCE_API_PRICING = {
    "Groq - Llama 3.1 8B Instant": {"input_per_1m": 0.05, "output_per_1m": 0.08},
    "OpenAI - GPT-4o mini":        {"input_per_1m": 0.15, "output_per_1m": 0.60},
}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# (1) Apenas a frase inicial (papel/persona) do prompt do especialista do
#     script de referencia. As instrucoes de extracao, calculo, passo a passo
#     e o JSON com "steps" foram removidas porque pertencem ao E1.
#     IMPORTANTE: manter esta MESMA frase como system prompt em E1..E7.
DIRECT_SYSTEM = """You are a financial analysis agent specializing in interpreting \
earnings reports and financial statements. Your task is to answer specific \
financial questions based on the given context from financial reports."""

# (2) Prompt do Experimento E0 (documento de ablacao) -- mantido INTEGRALMENTE.
E0_PROMPT_TEMPLATE = """You are a financial question answering assistant.

Use the provided financial context to answer the question.

Return only the final answer.

QUESTION:
{question}

TEXT:
{text}

TABLE:
{table}

HISTORY:
{history}
"""


# ---------------------------------------------------------------------------
# Estruturas de dados
# ---------------------------------------------------------------------------

@dataclass
class DirectResult:
    question_id: object
    ground_truth: str

    # resposta direta do LLM
    prediction: str = ""
    correct: bool = False
    raw_response: str = ""
    # como a resposta foi lida: json | numero_puro | apos_answer |
    # percentual | ultimo_numero_nao_ano | ultimo_numero | nenhum
    answer_extraction: str = ""
    parse_error: bool = False   # True se nenhum numero foi encontrado

    # tokens / custo operacional desta linha
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_seconds: float = 0.0
    llm_calls: int = 0


# ---------------------------------------------------------------------------
# Utilitarios de parsing / comparacao de respostas
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> dict:
    """Extrai o ULTIMO objeto JSON completo e valido da resposta do modelo.

    Os modelos pequenos costumam escrever texto com varios blocos JSON no
    meio (ex.: resultados parciais e depois o final). A versao anterior
    pegava tudo entre o primeiro "{" e o ultimo "}", o que juntava varios
    blocos num JSON invalido. Agora cada objeto de nivel superior e
    delimitado pelo balanceamento das chaves (ignorando chaves dentro de
    strings) e o ultimo que for valido e devolvido."""
    if text is None:
        return {"raw": "", "parse_error": True}
    candidates = []
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth > 0:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start:i + 1])
                start = None
    for cand in reversed(candidates):
        for attempt in (cand, cand.replace("'", '"')):
            try:
                obj = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    return {"raw": text, "parse_error": True}


def _extract_content(reply) -> str:
    """Extrai com seguranca o texto de uma resposta do AutoGen, que pode vir
    como str, dict ({'content': ...}) ou None (timeout/erro de conexao com
    o Ollama). Retorna sempre uma string (vazia se nao houver conteudo)."""
    if reply is None:
        return ""
    if isinstance(reply, str):
        return reply
    if isinstance(reply, dict):
        return reply.get("content", "") or ""
    return str(reply)


def _to_float(x):
    if x is None:
        return None
    s = str(x).strip().replace(",", "").replace("%", "").replace("$", "")
    try:
        return float(s)
    except ValueError:
        return None


def _answers_match(a, b, tol: float = 1e-2) -> bool:
    fa, fb = _to_float(a), _to_float(b)
    print ("FNFNFN Este caso!!")
    if fa is None or fb is None:
        return str(a).strip() == str(b).strip()
    return abs(fa - fb) <= tol * max(1.0, abs(fb))


# ---------------------------------------------------------------------------
# METRICAS
# ---------------------------------------------------------------------------
# Operam em cima do DataFrame de resultados (out_df) depois que todas as
# linhas foram processadas. Nao fazem nenhuma chamada ao LLM.
# Colunas usadas: prediction, correct, ground_truth.

def _pair_valid_floats(pred_series: pd.Series, gt_series: pd.Series):
    """Devolve duas listas (pred, gt) so com pares onde AMBOS os valores sao
    numericos validos. Usado para MAE / erro relativo / MSE, que nao fazem
    sentido para respostas nao-numericas ou nao-parseaveis."""
    preds, gts = [], []
    for p, g in zip(pred_series, gt_series):
        fp, fg = _to_float(p), _to_float(g)
        if fp is not None and fg is not None:
            preds.append(fp)
            gts.append(fg)
    return preds, gts


# ---------------------------------------------------------------------------
# EM com tolerancia de escala percentual
# ---------------------------------------------------------------------------
# No ConvFinQA os percentuais estao no gabarito como FRACAO (36.93% = 0.3693),
# mas alguns poucos estao em pontos percentuais. Um modelo que responde
# "36.93" para uma pergunta de percentual acerta o conteudo e erra so a
# convencao. O EM estrito continua sendo o criterio principal; o
# EM_escala_percentual aceita, SOMENTE em perguntas de percentual/razao,
# tambem prediction == gold*100 ou prediction == gold/100. E a metrica
# recomendada para comparar E0..E7, porque nao depende de cada prompt
# conhecer a convencao do dataset.

_PERCENT_Q_RE = re.compile(
    r"(?i)percent|%|\bportion\b|proportion|\bratio\b|represent|return rate|growth rate")


def is_percent_question(question: str) -> bool:
    return bool(_PERCENT_Q_RE.search(str(question or "")))


def answers_match_pct_scale(pred, gold, question: str) -> bool:
    if _answers_match(pred, gold):
        return True
    if not is_percent_question(question):
        return False
    fp, fg = _to_float(pred), _to_float(gold)
    if fp is None or fg is None:
        return False
    return _answers_match(fp, fg * 100) or _answers_match(fp, fg / 100)


def add_question_columns(out_df: pd.DataFrame, subset_df: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta ao CSV a pergunta respondida, se ela e de percentual e o
    acerto com tolerancia de escala percentual."""
    if len(out_df) == 0:
        return out_df
    qs = [build_fields(subset_df.loc[qid])["question"] if qid in subset_df.index else ""
          for qid in out_df["question_id"]]
    out_df.insert(out_df.columns.get_loc("ground_truth"), "question", qs)
    out_df["is_percent_question"] = [is_percent_question(q) for q in qs]
    out_df["correct_escala_pct"] = [answers_match_pct_scale(p, g, q) for p, g, q in
                                    zip(out_df["prediction"], out_df["ground_truth"], qs)]
    return out_df


def compute_answer_level_metrics(out_df: pd.DataFrame) -> Dict[str, float]:
    """Exact Match / Accuracy, MAE e erro relativo medio da resposta direta,
    comparada ao gabarito (ground_truth)."""
    metrics: Dict[str, float] = {}
    n = len(out_df)
    if n == 0:
        return metrics

    metrics["EM"] = 100.0 * out_df["correct"].sum() / n
    if "correct_escala_pct" in out_df.columns:
        metrics["EM_escala_percentual"] = 100.0 * out_df["correct_escala_pct"].sum() / n
        metrics["n_perguntas_percentual"] = int(out_df["is_percent_question"].sum())

    preds, gts = _pair_valid_floats(out_df["prediction"], out_df["ground_truth"])
    if preds:
        abs_errors = [abs(p - g) for p, g in zip(preds, gts)]
        rel_errors = [abs(p - g) / max(1.0, abs(g)) for p, g in zip(preds, gts)]
        metrics["MAE"] = sum(abs_errors) / len(abs_errors)
        metrics["ErroRelativoMedio"] = 100.0 * sum(rel_errors) / len(rel_errors)
    else:
        metrics["MAE"] = float("nan")
        metrics["ErroRelativoMedio"] = float("nan")
    metrics["n_pares_numericos"] = len(preds)
    return metrics


def compute_squared_error_metrics(out_df: pd.DataFrame) -> Dict[str, float]:
    """MSE (Erro Quadratico Medio) e RMSE (sua raiz) da resposta direta.

    Complementa o MAE: o MSE eleva os erros ao quadrado antes de tirar a
    media, entao penaliza mais fortemente respostas que erram "feio" (ex.:
    o modelo erra a ordem de grandeza), enquanto o MAE trata todo desvio
    de forma linear."""
    preds, gts = _pair_valid_floats(out_df["prediction"], out_df["ground_truth"])
    if not preds:
        return {"MSE": float("nan"), "RMSE": float("nan"), "n_pares_numericos_mse": 0}
    mse = sum((p - g) ** 2 for p, g in zip(preds, gts)) / len(preds)
    return {"MSE": mse, "RMSE": math.sqrt(mse), "n_pares_numericos_mse": len(preds)}


def _z_for_confidence(confidence: float) -> float:
    """Valor critico z (distribuicao normal padrao, bicaudal) para o nivel
    de confianca pedido. Usa scipy quando disponivel (qualquer confianca);
    caso contrario, cai para uma tabela com os niveis mais comuns."""
    if SCIPY_AVAILABLE:
        return float(scipy_stats.norm.ppf(1 - (1 - confidence) / 2))
    z_table = {0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}
    return z_table.get(round(confidence, 2), 1.9600)  # default: 95%


def _wilson_confidence_interval(successes: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """Intervalo de Confianca de Wilson para uma proporcao (EM/Accuracy).

    Preferido ao intervalo normal/Wald classico porque continua confiavel
    com amostras pequenas (ex.: n=100) e quando a proporcao observada esta
    perto de 0% ou 100% -- casos em que o intervalo de Wald pode extrapolar
    para fora de [0%, 100%]."""
    if n == 0:
        return float("nan"), float("nan")
    z = _z_for_confidence(confidence)
    p = successes / n
    denom = 1 + (z ** 2) / n
    centre = p + (z ** 2) / (2 * n)
    margin = z * math.sqrt((p * (1 - p) / n) + (z ** 2) / (4 * n ** 2))
    low = (centre - margin) / denom
    high = (centre + margin) / denom
    return max(0.0, low), min(1.0, high)


def compute_confidence_interval(out_df: pd.DataFrame, confidence: float = 0.95) -> Dict[str, float]:
    """Intervalo de Confianca (Wilson, 95% por padrao) para o EM.

    Com amostras pequenas (ex.: n=100), duas taxas de EM proximas (ex.:
    48% vs 45% entre E0 e E1) podem nao ser estatisticamente diferentes --
    o IC ajuda a visualizar essa incerteza."""
    n = len(out_df)
    if n == 0:
        return {}
    low, high = _wilson_confidence_interval(int(out_df["correct"].sum()), n, confidence)
    metrics = {
        "EM_IC_low": 100.0 * low,
        "EM_IC_high": 100.0 * high,
        "EM_IC_margem": 100.0 * (high - low) / 2,
        "IC_nivel_confianca": confidence,
        "IC_n_amostras": n,
    }
    if "correct_escala_pct" in out_df.columns:
        low, high = _wilson_confidence_interval(int(out_df["correct_escala_pct"].sum()), n, confidence)
        metrics["EM_escala_pct_IC_low"] = 100.0 * low
        metrics["EM_escala_pct_IC_high"] = 100.0 * high
    return metrics


def compute_unit_heuristic_accuracy(subset_df: pd.DataFrame, out_df: pd.DataFrame) -> Dict[str, float]:
    """Aproximacao HEURISTICA de Unit Accuracy (nao usa LLM): para perguntas
    cujo enunciado sugere uma unidade (%, milhao, bilhao), verifica se a
    resposta esta na mesma ordem de grandeza do gabarito (desvio relativo
    < 50%). Um erro de unidade (ex.: 0.25 em vez de 25%) cai fora dessa
    faixa. E um proxy grosseiro.

    As linhas sao pareadas por question_id (indice do CSV), entao linhas
    que falharam durante a execucao nao desalinham a comparacao."""
    def unit_hint(text: str) -> Optional[str]:
        t = str(text).lower()
        if "%" in t or "percent" in t:
            return "percent"
        if "billion" in t:
            return "billion"
        if "million" in t:
            return "million"
        return None

    matches = 0
    total = 0
    for _, row in out_df.iterrows():
        qid = row["question_id"]
        if qid not in subset_df.index:
            continue
        if unit_hint(subset_df.at[qid, TEXT_COL]) is None:
            continue
        total += 1
        fa, fg = _to_float(row["prediction"]), _to_float(row["ground_truth"])
        if fa is None or fg is None:
            continue
        if fg != 0 and abs(fa - fg) / max(1.0, abs(fg)) < 0.5:
            matches += 1
    return {
        "unit_accuracy_heuristica": 100.0 * matches / total if total else float("nan"),
        "n_perguntas_com_unidade_detectada": total,
    }


# ---------------------------------------------------------------------------
# Configuracao AutoGen
# ---------------------------------------------------------------------------

def _base_llm_config(temperature: float, model: str = OLLAMA_MODEL) -> dict:
    return {
        "config_list": [{
            "model": model,
            "base_url": OLLAMA_BASE_URL,
            "api_key": OLLAMA_API_KEY,
        }],
        "temperature": temperature,
        "timeout": TIMEOUT_SECONDS,
        "cache_seed": None,  # desliga cache do autogen p/ nao mascarar tokens/custos
    }



def build_agents():
    direct = AssistantAgent(
        name="Direct_LLM",
        system_message=DIRECT_SYSTEM,
        llm_config=_base_llm_config(DIRECT_TEMPERATURE),  # parece não ter nada sobre a questão que vá para o prompt
        human_input_mode="NEVER",
        max_consecutive_auto_reply=1,
        code_execution_config=False,
    )
    return direct


def _usage_totals(agents: List) -> Dict[str, int]:
    """Soma prompt/completion/total tokens de uma lista de agentes autogen,
    usando autogen.gather_usage_summary (cobre todos os modelos usados)."""
    summary = autogen.gather_usage_summary(agents)
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    usage = summary.get("usage_including_cached_inference", {})
    for model_name, stats in usage.items():
        if model_name == "total_cost":
            continue
        totals["prompt_tokens"] += stats.get("prompt_tokens", 0)
        totals["completion_tokens"] += stats.get("completion_tokens", 0)
        totals["total_tokens"] += stats.get("total_tokens", 0)
    return totals




# ---------------------------------------------------------------------------
# Montagem do prompt E0 (QUESTION / TEXT / TABLE / HISTORY)
# ---------------------------------------------------------------------------

_FIRST_TURN_RE = re.compile(r"(?i)(?:^|\n)[ \t]*question[ \t]*:")
_TURN_RE = re.compile(
    r"(?is)question[ \t]*:[ \t]*(.*?)(?:\n[ \t]*answer[ \t]*:[ \t]*(.*?))?[ \t]*(?=\n[ \t]*question[ \t]*:|\Z)")


def _cell(row: pd.Series, col: Optional[str]) -> Optional[str]:
    """Le uma coluna opcional do CSV; devolve None se nao existir/vazia."""
    if not col or col not in row.index:
        return None
    val = row[col]
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    s = str(val).strip()
    return s or None


def split_dialogue(full_text: str) -> Tuple[str, List[Tuple[str, str]], Optional[str]]:
    """Separa o texto do ConvFinQA em (contexto, historico, ultima pergunta).

    No CSV, a coluna "text" traz o relatorio (texto + tabela) seguido do
    dialogo: "Question: ... Answer: ... Question: ...". A ultima pergunta
    (sem resposta) e a que deve ser respondida; os pares anteriores sao o
    historico da conversa."""
    m = _FIRST_TURN_RE.search(full_text)
    if not m:
        return full_text.strip(), [], None
    context = full_text[:m.start()].strip()
    turns = [(q.strip(), (a or "").strip()) for q, a in _TURN_RE.findall(full_text[m.start():])]
    turns = [t for t in turns if t[0]]
    if not turns:
        return context, [], None
    return context, turns[:-1], turns[-1][0]


def build_fields(row: pd.Series) -> Dict[str, str]:
    """Preenche os blocos QUESTION / TEXT / TABLE / HISTORY (igual em E0..E7).

    - Colunas separadas (QUESTION_COL, TABLE_COL, HISTORY_COL), se existirem
      no CSV, sao usadas diretamente.
    - Caso contrario, a coluna "text" e dividida por split_dialogue():
      TEXT = relatorio (texto + tabela), QUESTION = ultima pergunta do
      dialogo e HISTORY = perguntas e respostas anteriores. Na versao
      anterior o historico ia misturado no TEXT e HISTORY aparecia como
      "(none)" mesmo em conversas de varios turnos.
    - Se nao houver "Question:" no texto, tudo vai para TEXT e QUESTION
      indica que a pergunta esta nele. Nenhuma informacao e descartada.
    """
    full_text = _cell(row, TEXT_COL) or ""
    question = _cell(row, QUESTION_COL)
    table = _cell(row, TABLE_COL)
    history = _cell(row, HISTORY_COL)

    text = full_text
    if question is None:
        context, turns, last_q = split_dialogue(full_text)
        if last_q:
            text, question = context, last_q
            if history is None and turns:
                history = "\n".join(f"Question: {q}\nAnswer: {a}" for q, a in turns)
        else:
            question = "(The question is included at the end of the TEXT block.)"

    return {
        "question": question,
        "text": text if text else "(not provided)",
        "table": table if table else "(not provided separately -- any table is included in the TEXT block)",
        "history": history if history else "(none)",
    }


def build_e0_prompt(row: pd.Series) -> str:
    return E0_PROMPT_TEMPLATE.format(**build_fields(row))


_NUM_RE = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?%?")


def _clean_num(tok: str) -> Optional[str]:
    t = tok.replace("$", "").replace(",", "").replace("%", "").strip()
    return t if _to_float(t) is not None else None


def _is_year_like(tok: str) -> bool:
    t = tok.replace("$", "").replace(",", "")
    return bool(re.fullmatch(r"(19|20)\d{2}", t))


def extract_direct_answer(raw: str) -> Tuple[Optional[str], str]:
    """Le a resposta do LLM direto. O prompt E0 pede "Return only the final
    answer" (texto livre), entao a leitura segue esta ordem:
      1. JSON com campo "answer" (se o modelo produzir JSON por conta propria)
      2. resposta inteira e so um numero (ex.: "25", "25%", "$1,200")
      3. numero logo apos "answer is" / "answer:" / "="
      4. ultimo numero com "%" (perguntas de percentual)
      5. ultimo numero que nao parece um ano (1900-2099)
      6. ultimo numero qualquer
    Devolve (resposta, modo_de_extracao)."""
    text = (raw or "").strip()
    if not text:
        return None, "nenhum"

    parsed = _parse_json_response(text)
    if not parsed.get("parse_error") and parsed.get("answer") is not None:
        return str(parsed.get("answer")), "json"

    stripped = re.sub(r"(?i)\b(million|billion|thousand|percent)\b", "", text).strip(" .\n\t*")
    if _clean_num(stripped) is not None:
        return _clean_num(stripped), "numero_puro"

    m = re.search(r"(?i)(?:final answer|answer)\s*(?:is|:|=)?\s*[:=]?\s*(-?\$?\d[\d,]*(?:\.\d+)?%?)", text)
    if m and _clean_num(m.group(1)) is not None:
        return _clean_num(m.group(1)), "apos_answer"

    tokens = _NUM_RE.findall(text)
    pct = [t for t in tokens if t.endswith("%")]
    if pct:
        return _clean_num(pct[-1]), "percentual"
    non_year = [t for t in tokens if not _is_year_like(t)]
    if non_year:
        return _clean_num(non_year[-1]), "ultimo_numero_nao_ano"
    if tokens:
        return _clean_num(tokens[-1]), "ultimo_numero"
    return None, "nenhum"


# ---------------------------------------------------------------------------
# Fluxo E0: Contexto -> LLM -> Resposta (1 unica chamada, sem critico)
# ---------------------------------------------------------------------------

def run_direct(direct, row: pd.Series, ground_truth: str,
               verbose: bool = False) -> DirectResult:
    prompt = build_e0_prompt(row)
    print ("FNFNFNFN")
    print (prompt)
    print ("FNFNFNFN")
    
    t0 = time.perf_counter()
    reply = direct.generate_reply(messages=[{"role": "user", "content": prompt}])
    print ("FNFNFNFN")
    print (reply)
    print ("FNFNFNFN")
    latency = time.perf_counter() - t0

    raw = _extract_content(reply)
    answer, mode = extract_direct_answer(raw)
    prediction = str(answer)
    parse_error = answer is None

    if verbose:
        print(f"    [{direct.name}] -> {prediction}  (extracao: {mode})")

    return DirectResult(
        question_id=None,
        ground_truth=str(ground_truth),
        prediction=prediction,
        correct=_answers_match(prediction, ground_truth),
        raw_response=raw,
        answer_extraction=mode,
        parse_error=parse_error,
        latency_seconds=latency,
        llm_calls=1,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _fmt(x, spec=".2f"):
    try:
        return "N/D" if x is None or (isinstance(x, float) and math.isnan(x)) else format(x, spec)
    except (TypeError, ValueError):
        return str(x)


def main():
    df = pd.read_csv(CSV_PATH)
    subset = df.head(MAX_ITEMS)

    results: List[DirectResult] = []
    n_correct = 0

    print(f"Experimento: {EXPERIMENT_ID}")
    print(f"Modelo: {OLLAMA_MODEL} | Endpoint: {OLLAMA_BASE_URL}")
    print(f"Processando {len(subset)} de {len(df)} linhas do CSV (MAX_ITEMS={MAX_ITEMS})")
    print(f"LLM direto (temperatura {DIRECT_TEMPERATURE}) | 1 chamada por pergunta | "
          "sem critico, sem PoT, sem calculadora\n")

    for i, (idx, row) in enumerate(subset.iterrows(), start=1):
        ground_truth = row[GOLD_COL]

        print(f"[{i}/{len(subset)}] Processando linha id={idx} ...")

        direct = build_agents()
        usage_before = _usage_totals([direct])

        try:
            result = run_direct(direct, row, ground_truth, verbose=VERBOSE)
            print ("FNFNFNFNFNFN")
            print (direct) #
            print (direct.system_message) #.llm_config parece não ter nada sobre a questão que vá para o prompt. .system_message parece não conter a pergunta...
            print (row)
            print (row.text)
            print (row.instruction)
            print ("FNFNFNFNFNFN")
            print (result)
            print ("FNFNFNFNFNFN")
        except Exception as e:
            print(f"  ERRO: {e}", file=sys.stderr)
            continue

        usage_after = _usage_totals([direct])
        result.question_id = idx
        result.prompt_tokens = usage_after["prompt_tokens"] - usage_before["prompt_tokens"]
        result.completion_tokens = usage_after["completion_tokens"] - usage_before["completion_tokens"]
        result.total_tokens = usage_after["total_tokens"] - usage_before["total_tokens"]

        results.append(result)
        n_correct += int(result.correct)

        print(f"  -> prediction: {result.prediction} (correto: {result.correct}) | "
              f"gt: {result.ground_truth} | tokens: {result.total_tokens} | "
              f"latencia: {result.latency_seconds:.2f}s\n")

        time.sleep(0.1)
        break; # FN - só executa o primeiro caso da linha

    # ------------------------------------------------------------------
    # Salva resultados detalhados (1 linha por pergunta)
    # ------------------------------------------------------------------
    out_df = pd.DataFrame([{
        "experiment": EXPERIMENT_ID,
        "question_id": r.question_id,
        "ground_truth": r.ground_truth,
        "prediction": r.prediction,
        "correct": r.correct,
        "answer_extraction": r.answer_extraction,
        "parse_error": r.parse_error,
        "raw_response": r.raw_response,
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "total_tokens": r.total_tokens,
        "latency_seconds": r.latency_seconds,
        "llm_calls": r.llm_calls,
    } for r in results])
    out_df = add_question_columns(out_df, subset)
    out_df.to_csv(OUT_PATH, index=False)

    # Registro por exemplo no formato pedido no documento de ablacao.
    with open(PER_EXAMPLE_JSONL_PATH, "w", encoding="utf-8") as f:
        for r in results:
            gold_f, pred_f = _to_float(r.ground_truth), _to_float(r.prediction)
            f.write(json.dumps({
                "id": str(r.question_id),
                "gold": gold_f if gold_f is not None else r.ground_truth,
                "prediction": pred_f if pred_f is not None else (None if r.prediction == "None" else r.prediction),
                "correct": bool(r.correct),
                "latency": round(r.latency_seconds, 3),
                "llm_calls": r.llm_calls,
            }, ensure_ascii=False) + "\n")

    n = len(results)

    print("=" * 70)
    print(f"RESUMO - {EXPERIMENT_ID}: LLM direto (baseline, AutoGen)")
    print("=" * 70)
    if n == 0:
        print("Nenhuma linha processada com sucesso.")
        return

    total_prompt_tokens = sum(r.prompt_tokens for r in results)
    total_completion_tokens = sum(r.completion_tokens for r in results)
    total_tokens = sum(r.total_tokens for r in results)
    n_parse_errors = sum(int(r.parse_error) for r in results)
    total_latency = sum(r.latency_seconds for r in results)
    total_calls = sum(r.llm_calls for r in results)

    print(f"Linhas avaliadas:                          {n}")
    print(f"Respostas sem numero extraivel:            {n_parse_errors}/{n} "
          f"({100 * n_parse_errors / n:.2f}%)")
    print("Modo de extracao da resposta:")
    for mode, cnt in out_df["answer_extraction"].value_counts().items():
        print(f"    {mode:<24} {cnt}")

    # ------------------------------------------------------------------
    # Metricas
    # ------------------------------------------------------------------
    answer_metrics = compute_answer_level_metrics(out_df)
    squared_error_metrics = compute_squared_error_metrics(out_df)
    ci_metrics = compute_confidence_interval(out_df, confidence=0.95)
    unit_metrics = compute_unit_heuristic_accuracy(subset, out_df)

    print("\n" + "-" * 70)
    print("ACERTOS")
    print("-" * 70)
    print(f"Acertos: {n_correct}/{n} -> EM = {_fmt(answer_metrics.get('EM'))}%")
    print(f"IC {int(ci_metrics.get('IC_nivel_confianca', 0.95) * 100)}% (Wilson): "
          f"[{_fmt(ci_metrics.get('EM_IC_low'))}%, {_fmt(ci_metrics.get('EM_IC_high'))}%] "
          f"(margem +-{_fmt(ci_metrics.get('EM_IC_margem'))} p.p.)")
    print(f"EM com escala percentual: {_fmt(answer_metrics.get('EM_escala_percentual'))}% "
          f"[{_fmt(ci_metrics.get('EM_escala_pct_IC_low'))}%, {_fmt(ci_metrics.get('EM_escala_pct_IC_high'))}%] "
          f"(aceita gold x100 ou /100 nas {answer_metrics.get('n_perguntas_percentual', 0)} perguntas "
          "de percentual; use esta para comparar E0..E7)")

    print("\n" + "-" * 70)
    print("ERRO NUMERICO (apenas pares com resposta numerica valida)")
    print("-" * 70)
    print(f"MAE:                 {_fmt(answer_metrics.get('MAE'), '.4f')} "
          f"(n={answer_metrics.get('n_pares_numericos', 0)})")
    print(f"Erro relativo medio: {_fmt(answer_metrics.get('ErroRelativoMedio'))}%")
    print(f"MSE:                 {_fmt(squared_error_metrics.get('MSE'), '.4f')}")
    print(f"RMSE:                {_fmt(squared_error_metrics.get('RMSE'), '.4f')}")

    print("\n" + "-" * 70)
    print("UNIDADE (heuristico, sem LLM)")
    print("-" * 70)
    print(f"Unit accuracy heuristica: {_fmt(unit_metrics.get('unit_accuracy_heuristica'))}% "
          f"(n={unit_metrics.get('n_perguntas_com_unidade_detectada', 0)})")

    print("\n" + "-" * 70)
    print("CUSTO OPERACIONAL")
    print("-" * 70)
    print(f"Chamadas ao LLM:              {total_calls} ({total_calls / n:.2f} por pergunta)")
    print(f"Latencia total:               {total_latency:,.1f}s")
    print(f"Latencia media por pergunta:  {total_latency / n:,.2f}s")
    print(f"Prompt tokens (entrada):      {total_prompt_tokens:,}")
    print(f"Completion tokens (saida):    {total_completion_tokens:,}")
    print(f"Total tokens:                 {total_tokens:,}")
    print(f"Media de tokens por pergunta: {total_tokens / n:,.1f}")

    print("\n" + "-" * 70)
    print("COMPARACAO DE CUSTO HIPOTETICO (execucao real foi LOCAL/GRATUITA via Ollama)")
    print("-" * 70)
    print("Os valores abaixo simulam quanto custaria rodar o MESMO volume de "
          "tokens em uma API paga (precos de referencia, sujeitos a mudanca):\n")
    cost_metrics: Dict[str, float] = {}
    for provider, price in REFERENCE_API_PRICING.items():
        cost_input = (total_prompt_tokens / 1_000_000) * price["input_per_1m"]
        cost_output = (total_completion_tokens / 1_000_000) * price["output_per_1m"]
        cost_total = cost_input + cost_output
        cost_metrics[f"custo_hipotetico_usd__{provider}"] = cost_total
        print(f"  {provider}:")
        print(f"    ${price['input_per_1m']:.3f}/1M input, ${price['output_per_1m']:.3f}/1M output")
        print(f"    Custo estimado: ${cost_input:.4f} (input) + ${cost_output:.4f} (output) "
              f"= ${cost_total:.4f} total | ${cost_total / n:.5f} por pergunta")
    print("\n  Ollama (local, GPU/CPU proprios): $0.00 por chamada de API "
          "(custo real e apenas energia/hardware).")

    operational_metrics = {
        "experiment": EXPERIMENT_ID,
        "modelo": OLLAMA_MODEL,
        "temperatura": DIRECT_TEMPERATURE,
        "n_avaliadas": n,
        "n_sem_numero_extraivel": n_parse_errors,
        "llm_calls_total": total_calls,
        "llm_calls_por_pergunta": total_calls / n,
        "latencia_total_s": total_latency,
        "latencia_media_s": total_latency / n,
        "prompt_tokens_total": total_prompt_tokens,
        "completion_tokens_total": total_completion_tokens,
        "total_tokens": total_tokens,
        "tokens_medios_por_pergunta": total_tokens / n,
    }

    metrics_path = OUT_PATH.replace(".csv", "_metricas.csv")
    all_metrics = {
        **operational_metrics,
        **answer_metrics,
        **squared_error_metrics,
        **ci_metrics,
        **unit_metrics,
        **cost_metrics,
    }
    pd.DataFrame([all_metrics]).to_csv(metrics_path, index=False)

    print(f"\nResultados detalhados salvos em: {OUT_PATH}")
    print(f"Metricas agregadas salvas em:    {metrics_path}")
    print(f"Registro por exemplo salvo em:   {PER_EXAMPLE_JSONL_PATH}")


if __name__ == "__main__":
    main()
