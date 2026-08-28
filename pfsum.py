
# Requer Python v3.11+

pfsum_versao = "2.0.0" #(2026-08-28)

import os
import argparse
import hashlib
import threading
import queue
import stat
import zlib
import signal
import sys
import time



# tamanho do buffer de leitura reutilizado por cada tarefa
tamanho_bloco = 2**20
# tamanho e quantidade dos buffers usados na leitura antecipada de arquivos grandes
tamanho_bloco_antecipado = 2**22
blocos_antecipados_maximo = 3
# a leitura antecipada so compensa em arquivos com varios blocos
limite_antecipacao = 2*tamanho_bloco_antecipado
# sinalizador exigido pelo Windows para abrir o arquivo em modo binario
modo_binario = getattr(os, "O_BINARY", 0)
# teto de tarefas: cada uma custa uma thread do sistema e um buffer de leitura, entao um
# numero digitado errado (-np 100000) travaria a maquina em vez de dar erro
tarefas_maximo = 256

# barra de progresso: espera antes do primeiro desenho e intervalo entre os desenhos, em segundos
atraso_progresso = 0.5
intervalo_progresso = 0.1
# largura da barra, em colunas, e velocidade da marca da barra indeterminada, em colunas por segundo
largura_barra_minima = 8
largura_barra_maxima = 32
celulas_por_segundo = 10
# custo aproximado de cada arquivo, em bytes equivalentes: em arquivos pequenos o tempo
# nao vem dos bytes lidos, e sim de abrir e fechar cada um (usado so na estimativa de tempo)
custo_arquivo = 2**16
# desenho da barra, desligado pela opcao -sb
mostra_progresso = True
# celulas da barra: blocos quando a saida de erro aceita, senao ASCII
try:
    "█░".encode(sys.stderr.encoding or "ascii")
    celula_cheia, celula_vazia = "█", "░"
except (AttributeError, LookupError, UnicodeError):
    celula_cheia, celula_vazia = "#", "-"



def SIGINThandler(sig, frame):
    # a linha da barra fica pela metade se o encerramento pegar um desenho no meio
    try: Progresso.atual.limpa()
    except (NameError, AttributeError): pass
    sys.exit(0)
signal.signal(signal.SIGINT, SIGINThandler)



def nao_relata(lidos):
    """Relator vazio, usado quando o andamento da leitura nao interessa a ninguem"""



def calcula_hash(caminho_arquivo, fhash, tamanho, buf, view, relata=nao_relata):
    """Funcao que calcula o hash do arquivo (hashlib)

    Args:
        caminho_arquivo (str): caminho completo do arquivo
        fhash (hashlib built-in function): funcao hash usada
        tamanho (int): tamanho do arquivo, ou -1 se desconhecido
        buf (bytearray): buffer de leitura reutilizado pela tarefa que chama
        view (memoryview): visao do buffer, usada para evitar copias
        relata (function): recebe o tamanho de cada bloco lido, para a barra de progresso

    Returns:
        hash (str): hash calculado
    """
    # arquivo que cabe no buffer: uma unica leitura, sem criar objeto de arquivo
    if 0 < tamanho <= len(buf):
        descritor = os.open(caminho_arquivo, os.O_RDONLY | modo_binario)
        try: dados = os.read(descritor, tamanho)
        finally: os.close(descritor)
        # se o tamanho mudou entre a listagem e a leitura, refaz pelo caminho geral
        # (quem chama e que soma esses bytes, para nao chamar o relator por arquivo)
        if len(dados) == tamanho: return fhash(dados).hexdigest()

    h = fhash()
    atualiza = h.update
    with open(caminho_arquivo, "rb", buffering=0) as f:
        readinto = f.readinto
        while True:
            lidos = readinto(buf)
            if not lidos: break
            atualiza(view[:lidos])
            relata(lidos)
    return h.hexdigest()



def calcula_hash_zlib(caminho_arquivo, fhash, tamanho, buf, view, relata=nao_relata):
    """Funcao que calcula o hash do arquivo (zlib)

    Args:
        caminho_arquivo (str): caminho completo do arquivo
        fhash (built-in function): funcao hash usada
        tamanho (int): tamanho do arquivo, ou -1 se desconhecido
        buf (bytearray): buffer de leitura reutilizado pela tarefa que chama
        view (memoryview): visao do buffer, usada para evitar copias
        relata (function): recebe o tamanho de cada bloco lido, para a barra de progresso

    Returns:
        hash (str): hash calculado
    """
    if 0 < tamanho <= len(buf):
        descritor = os.open(caminho_arquivo, os.O_RDONLY | modo_binario)
        try: dados = os.read(descritor, tamanho)
        finally: os.close(descritor)
        # o valor inicial vai explicito: adler32 usa 1 por omissao
        if len(dados) == tamanho: return fhash(dados, 0).to_bytes(4).hex()

    valor = 0
    with open(caminho_arquivo, "rb", buffering=0) as f:
        readinto = f.readinto
        while True:
            lidos = readinto(buf)
            if not lidos: break
            valor = fhash(view[:lidos], valor)
            relata(lidos)
    return valor.to_bytes(4).hex()



def blocos_antecipados(caminho_arquivo):
    """Gerador que le o arquivo em outra tarefa, sobrepondo a leitura e o calculo do hash

    Args:
        caminho_arquivo (str): caminho completo do arquivo

    Yields:
        bloco (memoryview): bloco lido, na ordem do arquivo
    """
    livres, cheios = queue.SimpleQueue(), queue.SimpleQueue()
    for _ in range(blocos_antecipados_maximo):
        buf = bytearray(tamanho_bloco_antecipado)
        livres.put((buf, memoryview(buf)))

    def leitor():
        """Laco da tarefa que apenas le o arquivo, sem calcular nada"""
        try:
            with open(caminho_arquivo, "rb", buffering=0) as f:
                readinto = f.readinto
                while True:
                    par = livres.get()
                    if par[0] is None: break
                    lidos = readinto(par[0])
                    cheios.put((par, lidos, None))
                    if not lidos: break
        except BaseException as excecao:
            cheios.put((None, 0, excecao))

    threading.Thread(target=leitor, daemon=True).start()
    try:
        while True:
            par, lidos, excecao = cheios.get()
            if excecao is not None: raise excecao
            if not lidos: break
            yield par[1][:lidos]
            livres.put(par)
    finally:
        # libera o leitor caso o consumo termine antes do fim do arquivo
        livres.put((None, None))



def calcula_hash_antecipado(caminho_arquivo, fhash, relata=nao_relata):
    """Funcao que calcula o hash de um arquivo grande com leitura antecipada (hashlib)

    Args:
        caminho_arquivo (str): caminho completo do arquivo
        fhash (hashlib built-in function): funcao hash usada
        relata (function): recebe o tamanho de cada bloco lido, para a barra de progresso

    Returns:
        hash (str): hash calculado
    """
    h = fhash()
    atualiza = h.update
    for bloco in blocos_antecipados(caminho_arquivo):
        atualiza(bloco)
        relata(len(bloco))
    return h.hexdigest()



def calcula_hash_zlib_antecipado(caminho_arquivo, fhash, relata=nao_relata):
    """Funcao que calcula o hash de um arquivo grande com leitura antecipada (zlib)

    Args:
        caminho_arquivo (str): caminho completo do arquivo
        fhash (built-in function): funcao hash usada
        relata (function): recebe o tamanho de cada bloco lido, para a barra de progresso

    Returns:
        hash (str): hash calculado
    """
    valor = 0
    for bloco in blocos_antecipados(caminho_arquivo):
        valor = fhash(bloco, valor)
        relata(len(bloco))
    return valor.to_bytes(4).hex()



# versao de leitura antecipada de cada funcao de calculo
calculo_antecipado = {calcula_hash:calcula_hash_antecipado, calcula_hash_zlib:calcula_hash_zlib_antecipado}



class CalcHashFunctor(object):
    """Classe auxiliar usada no metodo de criacao do arquivo de hashes

    Attributes:
        calculo_hash (function): funcao que calcula o hash
        calculo_antecipado (function): versao com leitura antecipada, para arquivos grandes
        fhash (hashlib built-in function): algoritmo usado pela funcao calculo_hash
    """
    def __init__(self, calculo_hash=calcula_hash, fhash=hashlib.sha256):
        """Construtor da classe auxiliar

        Args:
            calculo_hash (function): funcao que calcula o hash
            fhash (function): define o algoritmo usado no processamento
        """
        self.fhash = fhash
        self.calculo_hash = calculo_hash
        self.calculo_antecipado = calculo_antecipado[calculo_hash]

    def __call__(self, caminho_arquivo, tamanho=-1, buf=None, view=None, relata=nao_relata):
        """Metodo chamado durante o processamento de arquivos"""
        if buf is None:
            buf = bytearray(tamanho_bloco)
            view = memoryview(buf)
        return self.calculo_hash(caminho_arquivo, self.fhash, tamanho, buf, view, relata)

    def antecipado(self, caminho_arquivo, relata=nao_relata):
        """Metodo chamado no processamento de arquivos grandes"""
        return self.calculo_antecipado(caminho_arquivo, self.fhash, relata)



# funcoes hash disponiveis; para extender as opcoes, ver: https://docs.python.org/3/library/hashlib.html#hashlib.algorithms_available
funcao_nome = {hashlib.md5:"md5", hashlib.sha1:"sha1", hashlib.sha256:"sha256", hashlib.sha512:"sha512", zlib.crc32:"crc32", zlib.adler32:"adler32"}
nome_funcao = {v:k for k,v in funcao_nome.items()}
separador_funcao = {" ?"+v.upper()+"*":k for k,v in  funcao_nome.items()}
funcao_separador = {v:k for k,v in separador_funcao.items()}
funcao_calculo = {hashlib.md5:calcula_hash, hashlib.sha1:calcula_hash, hashlib.sha256:calcula_hash, hashlib.sha512:calcula_hash, zlib.crc32:calcula_hash_zlib, zlib.adler32:calcula_hash_zlib}
funcao_functor = {k:CalcHashFunctor(v, k) for k,v in funcao_calculo.items()}
functor_funcao = {v:k for k,v in funcao_functor.items()}
# separadores na ordem inversa: a busca por linha para no primeiro encontrado
separadores_busca = list(separador_funcao)[::-1]



def checa_caminho(caminho):
    """Funcao auxiliar para verificar se o arquivo/diretorio passado como argumento existe"""
    if not os.path.exists(caminho): raise argparse.ArgumentTypeError("Arquivo ou diretorio invalido.")
    return caminho



def checa_funcao(fhash):
    """Funcao auxiliar para verificar se a funcao hash passada como argumento existe"""
    fhash = fhash.lower()
    if not fhash in nome_funcao: raise argparse.ArgumentTypeError("Funcao hash invalida. Disponiveis: " + ",".join(nome_funcao.keys()))
    return nome_funcao[fhash]



def checa_codificacao(codificacao):
    """Funcao auxiliar para verificar se a codificacao passada como argumento serve para texto

    O teste e uma codificacao de verdade: alem dos apelidos, aceita os nomes canonicos
    (utf-8, ascii, cp1252) que a pagina indicada na ajuda lista, e recusa os codecs que
    nao produzem texto (rot13, base64).
    """
    try: "a".encode(codificacao)
    except (LookupError, TypeError):
        raise argparse.ArgumentTypeError("Codificacao invalida. Exemplos: utf8, utf-8, utf-16, latin1, cp1252, ascii")
    return codificacao



def checa_tamanho(tamanho):
    """Funcao auxiliar para validar o parametro de tamanho dos arquivos grandes"""
    if tamanho.isdigit(): return int(tamanho)
    else:
        tamanho = tamanho.lower()
        temp = tamanho[:-1]
        for i in {'b':1, 'k':2**10, 'm':2**20, 'g':2**30, 't':2**40}.items():
            if tamanho[-1] == i[0]:
                if temp.isdigit(): return int(temp)*i[1]
        raise argparse.ArgumentTypeError("Tamanho invalido de arquivos grandes. Exemplos: 512, 512b, 512k, 512m, 512M, 512g, 512G")



def caminhada(caminho):
    """Gerador que percorre o sistema de arquivos, na mesma ordem de os.walk(topdown=False)

    O tamanho vem da propria listagem do diretorio (os.scandir), sem uma chamada extra
    ao sistema por arquivo, e os nomes relativos sao montados por concatenacao.

    Args:
        caminho (str): caminho do diretorio de arquivos

    Yields:
        (caminho_completo, nome, tamanho) (str, str, int): dados de cada arquivo encontrado
    """
    pilha = [(caminho, "")]
    while pilha:
        item = pilha.pop()

        # lista de arquivos de um diretorio ja expandido: emitida depois dos subdiretorios
        if type(item) is list:
            yield from item
            continue

        atual, relativo = item
        # o os.walk precisava de rstrip so para trocar por "" o "." que relpath devolve na
        # raiz; aqui o relativo da raiz ja e "", e cortar pontos estragaria o nome do
        # diretorio (um diretorio chamado "..." jogaria os arquivos dele na raiz)
        prefixo = (relativo + os.sep) if relativo else ""

        # diretorio ilegivel e ignorado, como no comportamento padrao de os.walk
        try:
            with os.scandir(atual) as iterador: entradas = list(iterador)
        except OSError: continue

        subdiretorios, arquivos = [], []
        for entrada in entradas:
            try: e_diretorio = entrada.is_dir()
            except OSError: e_diretorio = False

            if e_diretorio:
                # atalhos para diretorios nao sao percorridos nem listados
                try: e_atalho = entrada.is_symlink()
                except OSError: e_atalho = False
                if not e_atalho:
                    subdiretorios.append((entrada.path, (relativo + os.sep + entrada.name) if relativo else entrada.name))
            else:
                arquivos.append((entrada.path, prefixo + entrada.name, entrada.stat().st_size))

        pilha.append(arquivos)
        pilha.extend(reversed(subdiretorios))



def separa_nomes(itens, nomes):
    """Gerador auxiliar que guarda os nomes relativos e repassa o restante ao processamento

    Args:
        itens (iteravel): tuplas (caminho_completo, nome, tamanho)
        nomes (list): lista preenchida com os nomes relativos, na ordem de saida

    Yields:
        (caminho_completo, tamanho) (str, int)
    """
    for caminho_completo, nome, tamanho in itens:
        nomes.append(nome)
        yield caminho_completo, tamanho




def formata_tamanho(tamanho):
    """Funcao auxiliar que formata uma quantidade de bytes em unidades binarias compactas

    Args:
        tamanho (int / float): quantidade de bytes

    Returns:
        texto (str): quantidade formatada, no mesmo estilo do parametro -s (1.5G, 512M, ...)
    """
    unidades = ("B", "K", "M", "G", "T", "P")
    indice = 0
    tamanho = float(tamanho)
    while tamanho >= 1024 and indice < len(unidades) - 1:
        tamanho /= 1024
        indice += 1
    if indice == 0 or tamanho >= 100: return "{:.0f}{}".format(tamanho, unidades[indice])
    return "{:.1f}{}".format(tamanho, unidades[indice])



def formata_tempo(segundos):
    """Funcao auxiliar que formata uma duracao em mm:ss, ou h:mm:ss quando passa de uma hora

    Args:
        segundos (int / float): duracao em segundos

    Returns:
        texto (str): duracao formatada
    """
    if segundos < 0 or segundos > 359999: return "--:--"
    horas, resto = divmod(int(segundos), 3600)
    minutos, segundos = divmod(resto, 60)
    if horas: return "{}:{:02d}:{:02d}".format(horas, minutos, segundos)
    return "{:02d}:{:02d}".format(minutos, segundos)



class Progresso(object):
    """Barra de progresso redesenhada de tempos em tempos, e nao a cada arquivo

    Quem dispara o desenho e o relogio, e nao a quantidade de arquivos: uma arvore de
    milhoes de arquivos pequenos custa o mesmo que um arquivo enorme, dez linhas por
    segundo. Cada tarefa escreve apenas na sua posicao das listas de contadores, e a
    tarefa de desenho apenas le, entao nao ha travas no caminho quente.

    Mesmo assim os contadores so sao alimentados quando a barra esta na tela (ver
    "ativa"): as poucas operacoes por arquivo custam cerca de 5% em arvore de arquivos
    pequenos, onde as tarefas ja disputam o GIL a cada arquivo. Com a saida de erro
    redirecionada, ou com -sb, o tempo volta a ser o mesmo de antes da barra.

    Enquanto a descoberta dos arquivos continua os totais ainda crescem, e a barra e
    indeterminada; quando ela termina, a barra passa a mostrar a fracao concluida e a
    estimativa do tempo restante.

    O desenho vai para a saida de erro, e so quando ela e um terminal: a saida normal
    fica livre para ser redirecionada (-p).

    Attributes:
        arquivos (list): arquivos ja concluidos, uma posicao por tarefa
        lidos (list): bytes ja lidos, uma posicao por tarefa
        total_arquivos (int): arquivos descobertos ate agora
        total_bytes (int): bytes descobertos ate agora
        completo (bool): indica que a descoberta terminou e os totais sao finais
        ativa (bool): indica que a barra esta sendo desenhada, e que vale contar
    """

    # barra em exibicao, usada para apagar a linha antes de qualquer outra impressao
    atual = None

    def __init__(self, tarefas):
        """Construtor da barra de progresso

        Args:
            tarefas (int): numero de posicoes dos contadores, uma por tarefa
        """
        self.arquivos = [0]*tarefas
        self.lidos = [0]*tarefas
        self.total_arquivos = 0
        self.total_bytes = 0
        self.completo = False
        self.inicio = time.monotonic()
        self.fim = threading.Event()
        self.trava = threading.RLock()
        self.tarefa = None
        self.ativa = False
        self.desenhada = False
        self.colunas = 80

    def inicia(self):
        """Comeca a desenhar a barra, se ela estiver habilitada e a saida de erro for um terminal"""
        if not mostra_progresso: return
        try:
            if not sys.stderr.isatty(): return
        except (AttributeError, ValueError): return
        self.ativa = True
        Progresso.atual = self
        self.tarefa = threading.Thread(target=self.laco, daemon=True)
        self.tarefa.start()

    def encerra(self, concluido=True):
        """Encerra o desenho, deixando a ultima linha na tela ou apagando-a

        Args:
            concluido (bool): indica que o processamento chegou ao fim
        """
        self.fim.set()
        if self.tarefa is None: return
        self.tarefa.join()
        self.tarefa = None
        Progresso.atual = None
        if not self.desenhada: return
        if concluido:
            self.completo = True
            self.escreve(self.linha() + "\n")
        else:
            self.limpa()

    def laco(self):
        """Laco da tarefa que apenas redesenha a barra de tempos em tempos"""
        # a primeira espera e maior: um processamento rapido termina sem barra nenhuma
        espera = atraso_progresso
        while not self.fim.wait(espera):
            espera = intervalo_progresso
            self.desenhada = True
            self.escreve(self.linha())

    def escreve(self, texto):
        """Escreve na saida de erro sem se misturar ao que as outras tarefas escrevem"""
        with self.trava:
            try:
                sys.stderr.write(texto)
                sys.stderr.flush()
            except Exception: pass

    def limpa(self, *mensagem):
        """Apaga a linha da barra e imprime a mensagem recebida, se houver

        A linha e apagada com espacos, e nao com sequencias de escape, porque nem todo
        console as reconhece.
        """
        with self.trava:
            if self.desenhada: self.escreve("\r" + " "*(self.colunas - 1) + "\r")
            if mensagem: print(*mensagem)

    def linha(self):
        """Monta a linha da barra, ajustada a largura do terminal

        Returns:
            texto (str): linha completa, ja com o retorno de carro do inicio
        """
        try: self.colunas = os.get_terminal_size(sys.stderr.fileno()).columns
        except (OSError, ValueError, AttributeError): pass

        decorrido = time.monotonic() - self.inicio
        arquivos = sum(self.arquivos)
        lidos = sum(self.lidos)

        fracao = 0.0
        if self.completo:
            total = self.total_bytes + self.total_arquivos*custo_arquivo
            if total > 0: fracao = min(1.0, (lidos + arquivos*custo_arquivo)/total)
            campos = ["{}/{} arq".format(arquivos, self.total_arquivos),
                      formata_tamanho(lidos) + "/" + formata_tamanho(self.total_bytes)]
        else:
            campos = ["{} arq".format(arquivos), formata_tamanho(lidos)]

        if decorrido > 0: campos.append(formata_tamanho(lidos/decorrido) + "/s")
        campos.append(formata_tempo(decorrido))
        if 0 < fracao < 1: campos.append("restam " + formata_tempo(decorrido*(1 - fracao)/fracao))

        # o percentual e truncado, e nao arredondado: 100% so aparece quando tudo terminou
        esquerda = "{:3d}% ".format(int(100*fracao)) if self.completo else "     "
        direita = "  ".join(campos)
        # em terminal estreito os ultimos campos saem fora, em vez de aparecerem pela metade
        while campos and len(esquerda) + len(direita) > self.colunas - 1:
            campos.pop()
            direita = "  ".join(campos)
        # a barra fica com o que sobrar da linha, e some quando o terminal e estreito demais
        largura = min(largura_barra_maxima, self.colunas - len(esquerda) - len(direita) - 5)
        if largura >= largura_barra_minima:
            if self.completo:
                cheias = int(largura*fracao)
                corpo = celula_cheia*cheias + celula_vazia*(largura - cheias)
            else:
                corpo = self.marca(largura, decorrido)
            direita = "[" + corpo + "]  " + direita
        return "\r" + (esquerda + direita)[:self.colunas - 1].ljust(self.colunas - 1)

    def marca(self, largura, decorrido):
        """Desenha o interior da barra indeterminada, usada enquanto a descoberta continua

        Args:
            largura (int): largura da barra, em colunas
            decorrido (float): tempo decorrido, que da a posicao da marca

        Returns:
            corpo (str): interior da barra
        """
        tamanho = max(1, largura//4)
        alcance = largura - tamanho
        if alcance < 1: return celula_cheia*largura
        posicao = int(decorrido*celulas_por_segundo) % (2*alcance)
        # a marca vai e volta entre as bordas
        if posicao > alcance: posicao = 2*alcance - posicao
        return celula_vazia*posicao + celula_cheia*tamanho + celula_vazia*(alcance - posicao)



def imprime(*mensagem):
    """Funcao auxiliar que imprime apagando antes a linha da barra, para nao embaralhar a saida"""
    barra = Progresso.atual
    if barra is None: print(*mensagem)
    else: barra.limpa(*mensagem)



def tarefa_hashes(fila, hashes, functor, e_lista, erros, antecipa, progresso, posicao):
    """Laco de uma tarefa auxiliar que consome arquivos da fila e grava os hashes

    Args:
        fila (queue.SimpleQueue): fila de tuplas (indice, caminho_completo, tamanho)
        hashes (list): lista de resultados, preenchida por indice
        functor (object / list): objeto callable ou lista de objetos
        e_lista (bool): indica se functor e uma lista indexada por arquivo
        erros (list): lista onde a primeira excecao encontrada e guardada
        antecipa (bool): habilita a leitura antecipada dos arquivos grandes
        progresso (Progresso): contadores da barra de progresso
        posicao (int): posicao desta tarefa nos contadores, escrita somente por ela
    """
    buf = bytearray(tamanho_bloco)
    view = memoryview(buf)
    feitos = 0
    cabe_no_buffer = len(buf)
    # sem barra na tela ninguem le os contadores, e alimenta-los custa alguns por cento
    # em arvores de arquivos pequenos, onde as tarefas ja disputam o GIL a cada arquivo
    ativa = progresso.ativa
    contador_arquivos, contador_bytes = progresso.arquivos, progresso.lidos

    relata = nao_relata
    if ativa:
        # a lista e a posicao vao por omissao: assim o laco abaixo nao passa a usar
        # celulas de fechamento, que sao mais lentas que as variaveis locais
        def relata(lidos, contador=contador_bytes, posicao=posicao):
            """Soma os bytes ja lidos do arquivo ainda em andamento"""
            contador[posicao] += lidos

    try:
        while True:
            item = fila.get()
            if item is None or erros: break
            indice, caminho_completo, tamanho = item
            f = functor[indice] if e_lista else functor
            # nos arquivos lidos em varios blocos a conta vem bloco a bloco, e nao no fim:
            # um arquivo grande sozinho deixaria a barra parada por minutos
            if antecipa and tamanho > limite_antecipacao: hashes[indice] = f.antecipado(caminho_completo, relata)
            else: hashes[indice] = f(caminho_completo, tamanho, buf, view, relata)
            if ativa:
                feitos += 1
                contador_arquivos[posicao] = feitos
                # o que coube no buffer foi lido de uma tacada, sem passar pelo relator
                if tamanho <= cabe_no_buffer: contador_bytes[posicao] += tamanho
    except BaseException as excecao:
        erros.append(excecao)



def processamento(itens, functor, tarefas, tamanho_grande):
    """Funcao que gera lista com os hashes calculados

    A descoberta dos arquivos, a leitura e o calculo acontecem ao mesmo tempo: os itens
    sao consumidos de um gerador e distribuidos assim que aparecem.

    Args:
        itens (iteravel): tuplas (caminho_completo, tamanho), na ordem de saida
        functor (object / list): objeto callable ou lista de objetos (usado para processar os arquivos)
        tarefas (int): numero de tarefas auxiliares para calcular os hashes de arquivos pequenos
        tamanho_grande (int): define o tamanho limite dos arquivos para processamento paralelo

    Returns:
        hashes (list): lista de hashes computados
    """
    hashes, erros = [], []
    e_lista = type(functor) is list
    # uma posicao de contador por tarefa de arquivos pequenos, mais uma para a dos grandes
    progresso = Progresso(tarefas + 1)
    progresso.inicia()
    concluido = False

    try:
        # execucao serial: um arquivo por vez, sem tarefas auxiliares de calculo
        if tarefas < 2:
            buf = bytearray(tamanho_bloco)
            view = memoryview(buf)
            cabe_no_buffer = len(buf)
            contador_bytes = progresso.lidos

            def relata(lidos, contador=contador_bytes):
                """Soma os bytes ja lidos do arquivo grande ainda em andamento"""
                contador[0] += lidos

            for caminho_completo, tamanho in itens:
                indice = len(hashes)
                hashes.append(None)
                progresso.total_arquivos = indice + 1
                progresso.total_bytes += tamanho
                f = functor[indice] if e_lista else functor
                if tamanho > limite_antecipacao: hashes[indice] = f.antecipado(caminho_completo, relata)
                else: hashes[indice] = f(caminho_completo, tamanho, buf, view, relata)
                progresso.arquivos[0] = indice + 1
                # o que coube no buffer foi lido de uma tacada, sem passar pelo relator
                if tamanho <= cabe_no_buffer: contador_bytes[0] += tamanho
            concluido = True
            return hashes

        fila_pequenos, fila_grandes = queue.SimpleQueue(), queue.SimpleQueue()
        trabalhadores = []
        for posicao in range(tarefas):
            trabalhadores.append(threading.Thread(target=tarefa_hashes, daemon=True,
                                                  args=(fila_pequenos, hashes, functor, e_lista, erros, False, progresso, posicao)))
        for trabalhador in trabalhadores: trabalhador.start()

        # arquivos grandes ficam em uma unica tarefa (evita competicao de I/O), em paralelo com os pequenos
        tarefa_grandes = None
        for caminho_completo, tamanho in itens:
            if erros: break
            indice = len(hashes)
            hashes.append(None)
            progresso.total_arquivos = indice + 1
            progresso.total_bytes += tamanho
            if tamanho <= tamanho_grande:
                fila_pequenos.put((indice, caminho_completo, tamanho))
            else:
                if tarefa_grandes is None:
                    tarefa_grandes = threading.Thread(target=tarefa_hashes, daemon=True,
                                                      args=(fila_grandes, hashes, functor, e_lista, erros, True, progresso, tarefas))
                    tarefa_grandes.start()
                fila_grandes.put((indice, caminho_completo, tamanho))

        # descoberta encerrada: os totais sao finais, e a barra pode estimar o tempo restante
        if not erros: progresso.completo = True

        for _ in trabalhadores: fila_pequenos.put(None)
        for trabalhador in trabalhadores: trabalhador.join()
        if tarefa_grandes is not None:
            fila_grandes.put(None)
            tarefa_grandes.join()

        if erros: raise erros[0]
        concluido = True
        return hashes
    finally:
        progresso.encerra(concluido)



def calcula_um(caminho, functor):
    """Funcao que calcula o hash de um unico arquivo, com barra de progresso nos grandes

    Args:
        caminho (str): caminho completo do arquivo
        functor (object): objeto callable (usado para processar o arquivo)

    Returns:
        hash (str): hash calculado
    """
    try: tamanho = os.path.getsize(caminho)
    except OSError: tamanho = -1
    if tamanho <= limite_antecipacao: return functor(caminho)

    progresso = Progresso(1)
    progresso.total_arquivos = 1
    progresso.total_bytes = tamanho
    progresso.completo = True
    contador_bytes = progresso.lidos

    def relata(lidos, contador=contador_bytes):
        """Soma os bytes ja lidos do arquivo"""
        contador[0] += lidos

    progresso.inicia()
    concluido = False
    try:
        digest = functor.antecipado(caminho, relata)
        progresso.arquivos[0] = 1
        concluido = True
        return digest
    finally:
        progresso.encerra(concluido)



def cria_texto(caminho, functor, tarefas, tamanho_grande):
    """Funcao que gera o texto a ser gravado no arquivo de hashes

    Args:
        caminho (str): caminho do arquivo ou diretorio de arquivos
        functor (object): objeto callable (usado para processar os arquivos)
        tarefas (int): numero de tarefas auxiliares para calcular os hashes de arquivos pequenos
        tamanho_grande (int): define o tamanho limite dos arquivos para processamento paralelo

    Returns:
        texto (str): lista de hashes computados
    """
    texto = ""
    separador = funcao_separador[functor_funcao[functor]]

    # se o argumento -d for diretorio, calcula os hashes dos arquivos presentes
    if os.path.isdir(caminho):
        nomes = []
        hashes = processamento(separa_nomes(caminhada(caminho), nomes), functor, tarefas, tamanho_grande)
        # toda linha termina em quebra de linha, inclusive a ultima: assim juntar dois
        # arquivos de hashes, ou acrescentar uma linha, nao corrompe a ultima entrada
        # (com a lista vazia o texto continua vazio, e nao vira uma linha em branco)
        texto = ''.join([hashes[i] + separador + nomes[i] + "\n" for i in range(len(hashes))])

    # se o argumento -d for arquivo, calcula o hash apenas de um arquivo
    else:
        texto = calcula_um(caminho, functor) + separador + (os.path.basename(caminho) if os.path.isfile(caminho) else caminho) + "\n"

    return texto



def ha_quem_responda():
    """Funcao auxiliar que indica se ha um terminal com alguem do outro lado

    No Windows o isatty() responde True tambem para o dispositivo NUL, que e justamente
    o que o agendador de tarefas costuma entregar como entrada padrao. Sem a segunda
    checagem, a execucao agendada cairia na pergunta, receberia fim de arquivo e
    cancelaria a gravacao calada. O console de verdade e atendido pelo _WindowsConsoleIO;
    o NUL, por um FileIO comum. A checagem so vale no Windows: no resto o terminal
    tambem e um FileIO, e o isatty() ja basta.

    Se a deteccao falhar (a variavel PYTHONLEGACYWINDOWSSTDIO, por exemplo, faz o console
    virar FileIO), o resultado e nao perguntar, ou seja, o comportamento de antes.

    Returns:
        (bool): indica se vale a pena perguntar alguma coisa
    """
    try:
        if not sys.stdin.isatty(): return False
    except (AttributeError, ValueError): return False
    if os.name != "nt": return True
    bruto = getattr(getattr(sys.stdin, "buffer", None), "raw", None)
    return type(bruto).__name__ != "FileIO"



def confirma_sobrescrita(resultado, perguntar):
    """Funcao que pede confirmacao quando o arquivo de saida ja existe

    A pergunta vem antes de qualquer calculo: descobrir no fim de uma arvore grande que
    o arquivo nao devia ser sobrescrito seria tarde demais.

    So pergunta quando ha alguem para responder (ver ha_quem_responda). Sem terminal, o
    comportamento continua sendo sobrescrever, como em todas as versoes anteriores: uma
    pergunta esperaria para sempre uma resposta que nunca vem, e cancelar por conta
    propria quebraria calada quem ja roda o script agendado.

    A pergunta sai na saida de erro, e nao na normal: assim ela aparece na tela mesmo
    quando a saida do script esta redirecionada para um arquivo.

    Args:
        resultado (str): caminho do arquivo de saida
        perguntar (bool): desligado por -sp, que sobrescreve sem perguntar

    Returns:
        (bool): indica se a gravacao pode continuar
    """
    if not perguntar or not os.path.exists(resultado): return True
    if not ha_quem_responda(): return True

    try:
        sys.stderr.write('O arquivo "{}" ja existe. Sobrescrever? (s/N) '.format(resultado))
        sys.stderr.flush()
        resposta = input()
    except (EOFError, OSError): return False

    if resposta.strip().lower() in ("s", "sim", "y", "yes"): return True
    sys.stderr.write("Operacao cancelada, o arquivo nao foi alterado.\n")
    return False



def cria_arquivo_hashes(caminho, resultado, codificacao, functor, tarefas, tamanho_grande, impressao):
    """Funcao que salva o arquivo de hashes com a codificacao especificada pelo usuario

    Args:
        caminho (str): caminho do arquivo ou diretorio de arquivos
        resultado (str): caminho do arquivo de saida
        codificacao (str): codificacao do arquivo de saida
        functor (object): objeto callable (usado para processar os arquivos)
        tarefas (int): numero de tarefas auxiliares para calcular os hashes de arquivos pequenos
        tamanho_grande (int): define o tamanho limite dos arquivos para processamento paralelo
        impressao (bool): imprime o resultado na tela em vez de gravar o arquivo
    """
    texto = cria_texto(caminho, functor, tarefas, tamanho_grande)
    if not len(texto):
        print("Nenhum arquivo encontrado em: " + caminho)
        return

    if impressao:
        # o texto ja termina em quebra de linha: o print nao pode acrescentar outra
        print(texto, end="")
    else:
        # a codificacao e testada antes de abrir o destino: abrir para escrita ja apaga o
        # arquivo de hashes anterior, e uma falha aqui deixaria um arquivo vazio no lugar
        try:
            texto.encode(codificacao)
        except UnicodeEncodeError as e:
            print(e)
            print('A codificacao "{}" nao representa todos os nomes: gravando em utf8'.format(codificacao))
            codificacao = "utf8"

        try:
            with open(resultado, "w", encoding=codificacao) as f:
                f.write(texto)
        except Exception as e:
            print(e)
            # so usado quando a escrita falha: importado aqui para nao atrasar a partida
            import tempfile
            # a codificacao vai junto, senao a gravacao de emergencia falha pelo mesmo motivo
            with tempfile.NamedTemporaryFile(mode='w', encoding=codificacao, suffix='.txt', prefix='hashes.', delete=False) as f:
                f.write(texto)
                resultado = f.name
            print("Resultado escrito no arquivo: " + resultado)

        print(functor(resultado) + funcao_separador[functor_funcao[functor]] + resultado)



def checa_hashes(resultado, codificacao, functor, tarefas, tamanho_grande):
    """Funcao que verifica o arquivo de hashes com a codificacao especificada pelo usuario e imprime na tela o resultado

    Args:
        resultado (str): caminho do arquivo de hashes
        codificacao (str): codificacao do arquivo de hashes
        functor (object): objeto callable usado para o calculo do hash do arquivo de hashes
        tarefas (int): numero de tarefas auxiliares para calcular os hashes de arquivos pequenos
        tamanho_grande (int): define o tamanho limite dos arquivos para processamento paralelo
    """
    integros, diferentes = 0, 0
    nao_encontrados, encontrados = [], []
    functors = []

    def linhas():
        """Gerador que le o arquivo de hashes e produz os arquivos a serem verificados

        Yields:
            (arquivo, tamanho) (str, int)
        """
        base = os.getcwd()
        contador_linha = 0
        with open(resultado, encoding=codificacao) as arquivo_hashes:
            for linha in arquivo_hashes:
                contador_linha += 1
                # marca de ordem de bytes: gravada por varios editores, ela entraria no
                # primeiro hash e a primeira linha nunca casaria
                if contador_linha == 1: linha = linha.lstrip("\ufeff")
                separador = False
                # o algoritmo de cada linha vem do separador encontrado nela
                for texto in separadores_busca:
                    if texto in linha:
                        separador = texto
                        break
                if not separador:
                    imprime("Erro ao ler a linha nº {}".format(contador_linha))
                    continue

                hash_arquivo = linha.split(separador)
                if len(hash_arquivo) == 2:
                    arquivo = os.path.join(base, hash_arquivo[1].rstrip("\n"))
                    # uma unica consulta ao sistema resolve existencia, tipo e tamanho
                    try: situacao = os.stat(arquivo)
                    except OSError: situacao = None
                    if situacao is None or not stat.S_ISREG(situacao.st_mode):
                        nao_encontrados.append(arquivo)
                    else:
                        encontrados.append([hash_arquivo[0], arquivo])
                        functors.append(funcao_functor[separador_funcao[separador]])
                        yield arquivo, situacao.st_size
                else:
                    imprime("Erro ao ler a linha nº {}".format(contador_linha))

    hashes = processamento(linhas(), functors, tarefas, tamanho_grande)
    for i in range(len(hashes)):
        if hashes[i] == encontrados[i][0].lower(): integros += 1
        else:
            print("Arquivo com hash direfente:", encontrados[i][1])
            diferentes += 1

    for nao_encontrado in nao_encontrados:
        print("Arquivo nao encontrado:", nao_encontrado)

    # calcula o hash do arquivo de hashes
    digest = functor(resultado)

    print("Total de Arquivos:", integros + diferentes + len(nao_encontrados),
          "\nIntegros:", integros,
          "\nDiferentes:", diferentes,
          "\nNao Encontrados:", len(nao_encontrados),
          "\n" + digest + funcao_separador[functor_funcao[functor]] + resultado)



if __name__ == '__main__':
    diretorio_corrente = os.getcwd()
    tarefas_padrao = int((os.cpu_count() or 1)/2)

    parser = argparse.ArgumentParser(prog = 'pfsum', description = "Calcula hashes ou verifica um arquivo de hashes, versao " + pfsum_versao)
    parser.add_argument('-c', help = "Modo de verificacao de hashes", action='store_true')
    parser.add_argument('-p', help = "Impressao na tela", action='store_true')
    parser.add_argument('-d', metavar = "Entrada", help = "Arquivo ou diretorio de entrada (default = diretorio corrente, ou hashes.txt no diretorio corrente no modo de verificacao)", type = checa_caminho, default = diretorio_corrente)
    parser.add_argument('-o', metavar = "Saida", help = "Arquivo de saida do resultado (default = hashes.txt no mesmo diretorio de entrada)")
    parser.add_argument('-fh', metavar = "Funcao", help = "Funcao hash usada. Disponiveis: {} (default = sha256)".format(', '.join(nome_funcao.keys())), type = checa_funcao, default = "sha256")
    parser.add_argument('-e', metavar = "Codificacao", help = "Tipo de codificacao do arquivo de entrada/saida (default = utf8, lista completa em https://docs.python.org/3/library/codecs.html#standard-encodings)", type = checa_codificacao, default = "utf8")
    parser.add_argument('-np', metavar = "Paralelismo", help = "Numero de tarefas usadas para calcular hashes de arquivos pequenos (default = {}, maximo = {})".format(tarefas_padrao, tarefas_maximo), type = int, default = tarefas_padrao)
    parser.add_argument('-s', metavar = "Tamanho", help = "Tamanho maximo de arquivo para processamento paralelo (default = 1G)", type = checa_tamanho, default = int(2**30))
    parser.add_argument('-sb', help = "Sem barra de progresso (a barra so aparece quando a saida de erro e um terminal)", action='store_true')
    parser.add_argument('-sp', help = "Sem pergunta: sobrescreve o arquivo de saida existente sem pedir confirmacao", action='store_true')

    argumentos = parser.parse_args()

    if argumentos.np < 2: argumentos.np = 1
    elif argumentos.np > tarefas_maximo: argumentos.np = tarefas_maximo
    if argumentos.s < 0: argumentos.s = 0
    if argumentos.sb: mostra_progresso = False

    # metodo de criacao do arquivo de hashes
    if not argumentos.c:
        resultado = os.path.abspath(argumentos.d)
        if os.path.isfile(argumentos.d): resultado = os.path.dirname(resultado)

        if argumentos.o: resultado = argumentos.o
        else: resultado = os.path.join(resultado, "hashes.txt")

        # com -p nada e gravado, entao nao ha o que sobrescrever nem o que perguntar
        if not argumentos.p and not confirma_sobrescrita(resultado, not argumentos.sp): sys.exit(0)

        cria_arquivo_hashes(argumentos.d, resultado, argumentos.e, funcao_functor[argumentos.fh], argumentos.np, argumentos.s, argumentos.p)

    # modo de verificacao de hashes
    else:
        if argumentos.d == diretorio_corrente: argumentos.d = os.path.join(diretorio_corrente, "hashes.txt")

        if not os.path.isfile(argumentos.d): print('Arquivo de entrada "{}" invalido para o modo de verificacao'.format(argumentos.d))
        else:
            checa_hashes(os.path.abspath(argumentos.d), argumentos.e, funcao_functor[argumentos.fh], argumentos.np, argumentos.s)
