2026-10-16

Este é o clone dos experimentos do Marco. Hoje pretendo executar um teste do convfinQA cuja resposta no E0 (somente analista financeiro) não bateu com o gabarito do questionário. Pretendo operar como documentei em https://github.com/FNakano/Agentes-Marco/tree/NoX.

Achei algo que talvez precise ser perguntado...
Fiz mais de uma execução do primeiro teste e a resposta uma vez foi 52.4 e em outras vezes foi 41 (não contei quantas vezes ao todo). Por outro lado, em `results_E0_direct_autogen_v2.csv` no github do Marco (https://github.com/marcodaq/Experimentos/tree/main/E0), no commit https://github.com/marcodaq/Experimentos/commit/e6884d8ea36dcd66301b92fa7876ceaf9baf330a , a resposta (coluna prediction) é igual à coluna ground_truth. Poderia pedir para ele mostrar a execução somente do primeiro teste...

**As respostas podem ser diferentes porque ele executa em GPU e eu em CPU - talvez alguma quantização foi feita sem que eu fosse avisado!**

Sobre a sequência de execução de  `python3  -m e0_direct_llm_finqa_autogen` (os números de linha incluem os comandos de impressão que inseri no programa):
  
O módulo tem função `main`. Esta função:
1. Lê a lista de questões armazenada em `CSV_PATH` (linha 728)). Na versão corrente:  `CSV_PATH = "convfinqa_amostra_100.csv"` (linha 133)
2. Escreve na tela dados sobre o experimento (linhas 734-738)
3. Executa cada exemplo em um loop (linhas 740-777)
   - o resultado de cada teste é armazenado como um item da lista `results`. Um particular item é armazenado na variável `result`
   - `result` armazena o valor de retorno da chamada da função `run direct(...)`. Na versão corrente: `result = run_direct(direct, row, ground_truth, verbose=VERBOSE)`
   - `run_direct()` é definida nas linhas 681-713
     1. constroi o prompt a ser processado pelo LLM (linha 683, função `build_e0_prompt`);
     2. executa (indiretamente) o LLM (linha 689, `reply = direct.generate_reply(messages=[{"role": "user", "content": prompt}])`)
     3. `direct` é uma instância criada pela chamada `direct = AssistantAgent(...)`, a função é definida no pacote `autogen` ié, a função `generate_reply` também é definda no pacote;
     4. A informação enviada ao LLM está armazenada (inteiramente e somente) na variável `prompt`;
        - O conteúdo da variável na execução da primeira questão é: 
        ```
       FNFNFNFN
You are a financial question answering assistant.

Use the provided financial context to answer the question.

Return only the final answer.

QUESTION:
what was the total balance of letters of credit outstanding for the years of 2006 and 2007, combined?

TEXT:
page 59 of 94 notes to consolidated financial statements ball corporation and subsidiaries 13 . debt and interest costs ( continued ) long-term debt obligations outstanding at december 31 , 2007 , have maturities of $ 127.1 million , $ 160 million , $ 388.4 million , $ 625.1 million and $ 550.3 million for the years ending december 31 , 2008 through 2012 , respectively , and $ 456.1 million thereafter . ball provides letters of credit in the ordinary course of business to secure liabilities recorded in connection with industrial development revenue bonds and certain self-insurance arrangements . letters of credit outstanding at december 31 , 2007 and 2006 , were $ 41 million and $ 52.4 million , respectively . the notes payable and senior credit facilities are guaranteed on a full , unconditional and joint and several basis by certain of the company 2019s domestic wholly owned subsidiaries . certain foreign denominated tranches of the senior credit facilities are similarly guaranteed by certain of the company 2019s wholly owned foreign subsidiaries . note 22 contains further details as well as condensed , consolidating financial information for the company , segregating the guarantor subsidiaries and non-guarantor subsidiaries . the company was not in default of any loan agreement at december 31 , 2007 , and has met all debt payment obligations . the u.s . note agreements , bank credit agreement and industrial development revenue bond agreements contain certain restrictions relating to dividend payments , share repurchases , investments , financial ratios , guarantees and the incurrence of additional indebtedness . on march 27 , 2006 , ball expanded its senior secured credit facilities with the addition of a $ 500 million term d loan facility due in installments through october 2011 . also on march 27 , 2006 , ball issued at a price of 99.799 percent $ 450 million of 6.625% ( 6.625 % ) senior notes ( effective yield to maturity of 6.65 percent ) due in march 2018 . the proceeds from these financings were used to refinance existing u.s . can debt with ball corporation debt at lower interest rates , acquire certain north american plastic container net assets from alcan and reduce seasonal working capital debt . ( see note 3 for further details of the acquisitions. ) on october 13 , 2005 , ball refinanced its senior secured credit facilities to extend debt maturities at lower interest rate spreads and provide the company with additional borrowing capacity for future growth . during the third and fourth quarters of 2005 , ball redeemed its 7.75% ( 7.75 % ) senior notes due in august 2006 . the refinancing and senior note redemptions resulted in a debt refinancing charge of $ 19.3 million ( $ 12.3 million after tax ) for the related call premium and unamortized debt issuance costs . a summary of total interest cost paid and accrued follows: . <table class='wikitable'><tr><td>1</td><td>( $ in millions )</td><td>2007</td><td>2006</td><td>2005</td></tr><tr><td>2</td><td>interest costs before refinancing costs</td><td>$ 155.8</td><td>$ 142.5</td><td>$ 102.4</td></tr><tr><td>3</td><td>debt refinancing costs</td><td>2013</td><td>2013</td><td>19.3</td></tr><tr><td>4</td><td>total interest costs</td><td>155.8</td><td>142.5</td><td>121.7</td></tr><tr><td>5</td><td>amounts capitalized</td><td>-6.4 ( 6.4 )</td><td>-8.1 ( 8.1 )</td><td>-5.3 ( 5.3 )</td></tr><tr><td>6</td><td>interest expense</td><td>$ 149.4</td><td>$ 134.4</td><td>$ 116.4</td></tr><tr><td>7</td><td>interest paid during the year ( a )</td><td>$ 153.9</td><td>$ 125.4</td><td>$ 138.5</td></tr></table> ( a ) includes $ 6.6 million paid in 2005 in connection with the redemption of the company 2019s senior and senior subordinated notes. .

TABLE:
(not provided separately -- any table is included in the TEXT block)

HISTORY:
(none)

FNFNFNFN
     ``` 
     5. A variável `reply` contém: 
     ```
     FNFNFNFN
The total balance of letters of credit outstanding for the years of 2006 and 2007, combined, was $93.4 million ($52.4 million in 2006 + $41 million in 2007).
FNFNFNFN
     ``` ... é texto puro, não é formatado em JSON
     6. Nas linhas 695-698 ficaram algumas funções de tratamento da resposta: 
     ```    raw = _extract_content(reply)
     answer, mode = extract_direct_answer(raw)
     prediction = str(answer)
     parse_error = answer is None
     ``` ... essas funções podem fazer aproximações, por exemplo, na linha 296, permite erro de 2%. É dessas funções que vem a coluna `answer extraction` com mensagem como `ultimo_numero_nao_ano`
4. Calcula as métricas e salva os resultados por teste e gerais de todas as execuções (linhas 779-929)

É meio fora do contexto, mas se ajustar uma frase do prompt de `Return only the final answer.` para `Return only the numerical final answer.` a resposta muda para 93 e se mudar essa frase para `Return only the final answer in floating point format.` o resultado é exact match...



