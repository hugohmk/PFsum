# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

PFsum é um único script Python (`pfsum.py`) que calcula e verifica códigos de autenticidade (hashes) de arquivos, compatível com o formato do Fsum (SlavaSoft). Não há dependências externas — só a stdlib — e não há build, empacotamento, testes automatizados nem configuração de lint.

Requer **Python 3.11+** (usa `hashlib.file_digest`, introduzido nessa versão).

Código, comentários, docstrings, mensagens ao usuário e README são em **português sem acentos nos identificadores**. Mantenha esse estilo ao editar.

## Comandos

```bash
# gerar hashes.txt do diretorio corrente (SHA256, recursivo)
python pfsum.py

# verificar um hashes.txt no diretorio corrente
python pfsum.py -c

# imprimir na tela sem gravar arquivo
python pfsum.py -p -d CAMINHO

# teste de fumaca manual (nao ha suite de testes): round-trip gerar -> verificar
cd /tmp/algum-dir && python /caminho/pfsum.py && python /caminho/pfsum.py -c
```

Opções relevantes: `-c` (verificação), `-p` (stdout), `-d` (entrada), `-o` (saída), `-fh` (algoritmo), `-e` (codificação), `-np` (nº de processos), `-s` (limite de tamanho para paralelismo), `-sb` (sem barra de progresso), `-sp` (sem pergunta de sobrescrita). O README tem a tabela completa e exemplos.

Ao alterar comportamento visível ao usuário, atualize `pfsum_versao` no topo de `pfsum.py` e a tabela de opções do `README.md`.

## Arquitetura

### Tabelas de registro dos algoritmos

O bloco de dicionários no topo do arquivo (`funcao_nome`, `nome_funcao`, `separador_funcao`, `funcao_separador`, `funcao_calculo`, `funcao_functor`, `functor_funcao`) é o núcleo do design: tudo no script navega entre *nome do algoritmo* ↔ *função hash* ↔ *separador do arquivo de saída* ↔ *functor de cálculo* por essas tabelas, em vez de `if/else`. **Adicionar um algoritmo exige entradas em `funcao_nome` e `funcao_calculo`** — as demais tabelas são derivadas por compreensão e se atualizam sozinhas.

O separador (`" ?SHA256*"`, `" ?MD5*"`, …) é o que dá compatibilidade com o Fsum: cada linha de saída é `<hash><separador><caminho relativo>`. Na verificação, o algoritmo de cada linha é deduzido do separador encontrado nela, e não de `-fh` — um mesmo arquivo de hashes pode misturar algoritmos. `-fh` na verificação serve apenas para calcular o hash do próprio arquivo de hashes.

### Dois caminhos de cálculo, cada um com duas variantes

`calcula_hash` (hashlib) e `calcula_hash_zlib` (crc32/adler32, que não têm interface de objeto hash e produzem 4 bytes hex). Cada um tem uma variante de leitura antecipada para arquivos grandes (`calcula_hash_antecipado`, `calcula_hash_zlib_antecipado`), ligada à versão direta pela tabela `calculo_antecipado`.

Dois detalhes que já causaram bug e não devem ser desfeitos:

- **O buffer de leitura é alocado por tarefa e reutilizado a cada arquivo**, nunca por arquivo. Alocar um `bytearray` de 1 MiB por arquivo (o que `hashlib.file_digest` faz internamente) custava ~550 µs por arquivo — mais que o hash em si nos arquivos pequenos.
- Nas funções zlib, o valor inicial vai **explícito** (`fhash(dados, 0)`): `zlib.adler32` usa 1 por omissão, e usar o padrão muda todos os hashes adler32 já gerados.

Arquivos que cabem no buffer são lidos por `os.open`/`os.read` em uma única chamada, sem criar objeto de arquivo — é isso que sustenta o paralelismo com threads (menos tempo segurando o GIL por arquivo). O tamanho vem da listagem do diretório; se ele não bater com o que foi lido, o cálculo é refeito pelo caminho geral.

### Duas filas: arquivos pequenos e grandes

`caminhada()` é um **gerador** que percorre o sistema de arquivos com `os.scandir` e tira o tamanho da própria listagem (no Windows isso não custa chamada extra ao sistema). Ele reproduz exatamente a ordem de `os.walk(topdown=False)`, inclusive o tratamento de atalhos e de diretórios ilegíveis — **a ordem das linhas do arquivo de hashes depende disso**, então qualquer mudança aqui precisa ser comparada com a saída anterior byte a byte.

O prefixo relativo é montado por concatenação e **não passa por `rstrip`**. O `os.walk` precisava dele só para trocar por `""` o `"."` que `os.path.relpath` devolve na raiz; aqui o relativo da raiz já é `""`, e cortar caracteres do fim comia pontos legítimos do nome do diretório — um diretório `sub.ponto.` virava `sub.ponto`, e um diretório `...` jogava os arquivos dele na raiz. Corrigido na 1.6.0. (Continua valendo que, no Windows, um diretório com ponto no fim só é alcançável por caminho estendido `\\?\`, então na prática ele é ignorado ao ser percorrido por caminho normal.)

`processamento()` consome esse gerador e distribui: arquivos até o limite `-s` vão para as tarefas paralelas, os maiores vão para uma **única** tarefa serial (evita competição de I/O), que roda em paralelo com as demais. Descoberta, leitura e cálculo acontecem ao mesmo tempo. O índice de cada arquivo é a posição em que ele foi produzido, e as tarefas gravam por índice em `hashes`, o que preserva a ordem de `nomes`.

É daí que vem a **ordem determinística da saída**, e ela não é acidental: numa árvore com tamanhos bem diferentes lado a lado, as conclusões chegam a sair 40 posições fora da ordem de produção. Só o produtor faz `hashes.append(None)`, e sempre na thread principal; as tarefas apenas escrevem em `hashes[indice]`, nunca acrescentam. Quem mexer aqui precisa manter as duas coisas — mover o `append` para dentro das tarefas quebraria a ordem sem quebrar teste nenhum dos que existem hoje.

### Threads, não processos

O paralelismo é feito com `threading` + `queue.SimpleQueue`, não com `multiprocessing`. Medido neste repositório: threads ganham dos processos mesmo ignorando o custo de partida do `Pool` (~280 ms no Windows), porque `hash.update()`, `os.read` e `open` liberam o GIL. Escala ~20x em arquivos grandes e ~5x em arquivos pequenos.

Consequências para edições:

- **Nada precisa mais ser picklável.** `CalcHashFunctor` continua sendo classe de nível de módulo por clareza, mas closures e geradores aninhados são permitidos (o produtor de `checa_hashes` é um).
- As tarefas são `daemon=True` e guardam a primeira exceção em `erros`, que `processamento()` relança na thread principal. Sem isso, uma falha em tarefa auxiliar deixaria `None` na lista de hashes.
- A distribuição é item a item, e não em blocos: com `p.map` o agrupamento estático jogava vários arquivos grandes no mesmo bloco e serializava a árvore mista (10,3 s contra 2,1 s).
- `-np` é limitado por `tarefas_maximo` (256). Cada tarefa custa uma thread do sistema **e** um buffer de leitura de 1 MiB, então um número digitado errado não dava erro: `-np 100000` passava de 300 s sem terminar numa pasta de três arquivos, tentando reservar ~100 GB de buffers.

### Functor único vs. lista de functors

`processamento()` aceita `functor` como um único callable (modo de criação, um algoritmo para todos) **ou** como uma lista indexada por arquivo (modo de verificação, algoritmo por linha). No modo de verificação a lista é preenchida pelo próprio gerador que lê o arquivo de hashes, sempre antes de o item ser entregue à fila.

### Barra de progresso

`Progresso` desenha em uma tarefa própria, disparada **pelo relógio** (`atraso_progresso` = 0,5 s até o primeiro desenho, depois `intervalo_progresso` = 0,1 s), nunca por arquivo processado: uma árvore de milhões de arquivos custa o mesmo que um arquivo enorme, dez linhas por segundo. O desenho sai na **saída de erro** e só quando ela é um terminal (e `-sb` não foi passado), então `-p > hashes.txt` continua limpo e as comparações byte a byte da saída seguem valendo.

Cada tarefa escreve só na sua posição de `progresso.arquivos`/`progresso.lidos`, e a tarefa de desenho só lê — sem travas. Três decisões medidas neste repositório:

- **Os contadores só são alimentados quando a barra está na tela** (`progresso.ativa`, lido uma vez por tarefa). Alimentá-los sempre custa ~5% em árvore de 20 mil arquivos pequenos, onde as tarefas já disputam o GIL a cada arquivo; com a guarda, o tempo com a saída de erro redirecionada volta a ser o de antes da barra. No caminho serial e no produtor não há guarda: lá é uma tarefa só, e a medição não distingue do ruído.
- **Quem lê em vários blocos se relata bloco a bloco** (`relata`, em `calcula_hash`, `calcula_hash_zlib` e nas duas versões antecipadas); só o que cabe no buffer de leitura é somado pelo chamador, depois do cálculo — por isso a condição `tamanho <= cabe_no_buffer` espelha a do caminho rápido. Sem isso a barra ficava parada durante um arquivo grande, que é justamente quando ela importa.
- **Os relatores recebem a lista e a posição por omissão** (`def relata(lidos, contador=..., posicao=...)`), para que as variáveis do laço continuem sendo locais rápidas em vez de células de fechamento.

Enquanto `caminhada()` ainda produz, os totais crescem e a barra é indeterminada (marca que vai e volta); quando o gerador acaba, `processamento()` marca `completo` e a barra passa a mostrar percentual e tempo restante. A fração usa `bytes + arquivos*custo_arquivo`, porque em arquivo pequeno o tempo vem de abrir e fechar, e não dos bytes lidos — e isso também resolve a árvore só de arquivos vazios.

Qualquer mensagem impressa com a barra na tela precisa sair por `imprime()`, que apaga a linha antes (com espaços, não com sequências de escape) — é o caso dos erros de leitura de linha em `checa_hashes`.

### Codificação e fallback de escrita

`-e` controla a codificação do arquivo de saída/entrada e aceita **qualquer nome que o Python reconheça como codificação de texto** — `checa_codificacao` valida com `"a".encode(...)`, o que também recusa codecs que não produzem texto (`rot13`, `base64`). A validação anterior, por `choices=encodings.aliases`, recusava justamente os nomes canônicos que a página indicada na ajuda lista: `utf-8`, `ascii`, `cp1252`, `latin-1` só passavam nas formas `utf8`, `latin1`.

A gravação tem três cuidados, todos por causa de falhas observadas:

- **O texto é codificado antes de o destino ser aberto.** Abrir para escrita já apaga o arquivo de hashes anterior, então uma falha de codificação depois disso trocava um arquivo bom por um de zero byte. Se a codificação pedida não representa todos os nomes, avisa e grava em utf8, em vez de perder o trabalho.
- **A gravação de emergência leva a mesma codificação.** Sem `encoding=` o `NamedTemporaryFile` usa a do sistema e falhava pelo mesmo motivo da primeira tentativa — o resultado era um traceback e dois arquivos vazios largados para trás.
- Se a escrita no destino falhar por outro motivo (permissão, caminho inválido), o resultado vai para o `NamedTemporaryFile` e o caminho é informado, como antes.

Ao final, imprime o hash do próprio arquivo de hashes.

### Confirmação de sobrescrita

`confirma_sobrescrita()` é chamada em `__main__`, **antes** de `cria_arquivo_hashes` e portanto antes de qualquer cálculo: perguntar depois de varrer uma árvore de horas não serviria para nada. Não pergunta com `-p` (nada é gravado), nem com `-c` (não há arquivo de saída), nem com `-sp`. Resposta vazia ou qualquer coisa fora de `s/sim/y/yes` cancela, e a saída é `sys.exit(0)` — foi escolha de quem estava na frente do teclado, não erro.

A pergunta sai na **saída de erro**, como a barra, para aparecer mesmo com a saída normal redirecionada.

`ha_quem_responda()` decide se vale perguntar, e a segunda checagem dela não é enfeite: **no Windows o `isatty()` responde `True` também para o dispositivo `NUL`**, que é o que o agendador de tarefas costuma entregar como entrada padrão. Só com `isatty()`, uma tarefa agendada cairia na pergunta, receberia fim de arquivo na hora e cancelaria a gravação calada — quebrando quem já roda o script agendado, sem nenhum sinal. O console de verdade é atendido pelo `_WindowsConsoleIO`; o `NUL`, por um `FileIO` comum, e é isso que os separa. A checagem vale só no Windows: no resto o terminal também é um `FileIO`, e ali o `isatty()` já basta. Se a distinção falhar (com `PYTHONLEGACYWINDOWSSTDIO`, por exemplo), o resultado é não perguntar, ou seja, o comportamento de sempre — nunca travar esperando.

Sem terminal o arquivo é sobrescrito como em todas as versões anteriores: é o que mantém script e agendador funcionando igual.

### Formato do arquivo de hashes

**Toda linha termina em quebra de linha, inclusive a última** (mudou na 1.6.0). Antes o arquivo terminava sem quebra, e juntar dois arquivos de hashes ou acrescentar uma linha corrompia a última entrada. `cria_texto` monta o texto com `''.join([... + "\n"])`, e não com `'\n'.join([...])`: com a lista vazia o texto continua vazio, e não vira uma linha em branco.

Diretório sem nenhum arquivo não gera saída: imprime `Nenhum arquivo encontrado em: ...` em vez de terminar calado.

Na verificação, uma marca de ordem de bytes (BOM) no começo do arquivo é descartada da primeira linha. Sem isso ela entrava no primeiro hash, e a primeira linha nunca casava — um "Diferentes: 1" sem explicação, em arquivo gravado por editor do Windows.

### Interrupção

Um handler de `SIGINT` registrado no topo do módulo chama `sys.exit(0)`, para que Ctrl+C encerre o script sem despejar traceback. Como as tarefas auxiliares são daemon, o processo sai na hora; não use threads não-daemon aqui, senão o Ctrl+C passa a esperar a fila terminar. Antes de sair, o handler apaga a linha da barra de progresso (a `trava` é reentrante justamente para isso: o handler roda na thread principal, que pode já estar segurando a trava).

## Verificando alterações

Não há suíte de testes no repositório. Para qualquer mudança nos caminhos de cálculo, na caminhada ou no paralelismo, compare a saída com a da versão anterior **byte a byte**, nos 6 algoritmos e nos dois modos:

```bash
git show HEAD:pfsum.py > /tmp/pfsum_orig.py
python /tmp/pfsum_orig.py -d ARVORE -o /tmp/a.txt && python pfsum.py -d ARVORE -o /tmp/b.txt && cmp /tmp/a.txt /tmp/b.txt
```

Desde a 1.6.0 a comparação com a versão anterior à correção não dá igual: o arquivo novo é **exatamente o antigo mais uma quebra de linha no fim**. Vale conferir que a diferença é só essa (`db == da + os.linesep.encode()`), nos 6 algoritmos e em todas as combinações de `-np` e `-s`, e que o `-c` das duas versões continua lendo o arquivo da outra.

Casos que já pegaram regressão: adler32 (valor inicial), diretórios com nome terminado em `.` (hoje o nome sai inteiro; antes o ponto era cortado), arquivos vazios, `-s 0`, `-s 10T`, `-np 1`, arquivo de hashes com algoritmos misturados e linhas inválidas, arquivo de hashes com BOM, e destino que não aceita a codificação pedida.

Para a confirmação de sobrescrita, teste os três tipos de entrada padrão, porque eles não se comportam igual: terminal (troque `sys.stdin` por um objeto cujo `isatty()` devolva `True`), canalização (`stdin=PIPE`) e **`NUL` (`stdin=DEVNULL`)**, que no Windows se diz terminal e é o caso que já quebrou. Nos dois últimos não pode aparecer pergunta nenhuma, e o arquivo tem de ser sobrescrito.

**Ordem da saída:** a ordem das linhas não pode depender de qual tarefa termina primeiro. Cruze `-np 1/2/3/5/8/24` com `-s 0/1k/100k/4m/1G/10T`, repetindo cada combinação, e exija saída idêntica byte a byte em todas — e igual à ordem de `os.walk(topdown=False)`. O teste só tem valor numa árvore que force conclusões fora de ordem (arquivos de 0 a 15 MB lado a lado, em vários subdiretórios); com arquivos todos do mesmo tamanho ele passa sem provar nada. Vale confirmar isso medindo as inversões na ordem de conclusão, com um functor que anote quando cada cálculo termina.

A barra não aparece nessas comparações (a saída de erro não é terminal quando redirecionada), então `cmp` continua valendo. Para exercitá-la sem terminal, troque `sys.stderr` por um objeto cujo `isatty()` devolva `True` e capture o que for escrito; vale conferir a barra indeterminada, a determinada, o terminal estreito (os campos saem antes de serem cortados), a linha final que fica na tela, e o caminho de erro, que apaga a linha em vez de deixá-la.
