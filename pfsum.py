
# Requer Python v3.11+

pfsum_versao = "1.3.0" #(2025-12-02)

import os
import argparse
import hashlib
import encodings
import multiprocessing
import tempfile
import zlib



def calcula_hash(f, fhash):
    """Funcao que calcula o hash do arquivo (hashlib)
    
    Args:
        f (file): arquivo aberto em modo leitura binaria ("rb")
        fhash (hashlib built-in function): funcao hash usada
    
    Returns:
        hash (str): hash calculado
    """
    return hashlib.file_digest(f, fhash, _bufsize=2**20).hexdigest()



def calcula_hash_zlib(f, fhash):
    """Funcao que calcula o hash do arquivo (zlib)
    
    Args:
        f (file): arquivo aberto em modo leitura binaria ("rb")
        fhash (built-in function): funcao hash usada
    
    Returns:
        hash (str): hash calculado
    """
    buf = bytearray(2**20)
    view = memoryview(buf)
    valor = 0
    while True:
        size = f.readinto(buf)
        if size == 0: break
        valor = fhash(view[:size], valor)
    return valor.to_bytes(4).hex()



class CalcHashFunctor(object):
    """Classe auxiliar usada no metodo de criacao do arquivo de hashes
    
    Attributes:
        calculo_hash (function): funcao que calcula o hash
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
    
    def __call__(self, caminho_arquivo):
        """Metodo chamado durante o processamento de arquivos"""
        with open(caminho_arquivo, "rb") as f:
            return self.calculo_hash(f, self.fhash)



def auxiliar(f, v): return f(v)



# funcoes hash disponiveis; para extender as opcoes, ver: https://docs.python.org/3/library/hashlib.html#hashlib.algorithms_available
funcao_nome = {hashlib.md5:"md5", hashlib.sha1:"sha1", hashlib.sha256:"sha256", hashlib.sha512:"sha512", zlib.crc32:"crc32", zlib.adler32:"adler32"}
nome_funcao = {v:k for k,v in funcao_nome.items()}
separador_funcao = {" ?"+v.upper()+"*":k for k,v in  funcao_nome.items()}
funcao_separador = {v:k for k,v in separador_funcao.items()}
funcao_calculo = {hashlib.md5:calcula_hash, hashlib.sha1:calcula_hash, hashlib.sha256:calcula_hash, hashlib.sha512:calcula_hash, zlib.crc32:calcula_hash_zlib, zlib.adler32:calcula_hash_zlib}
funcao_functor = {k:CalcHashFunctor(v, k) for k,v in funcao_calculo.items()}
functor_funcao = {v:k for k,v in funcao_functor.items()}



def checa_caminho(caminho):
    """Funcao auxiliar para verificar se o arquivo/diretorio passado como argumento existe"""
    if not os.path.exists(caminho): raise argparse.ArgumentTypeError("Arquivo ou diretorio invalido.")
    return caminho



def checa_funcao(fhash):
    """Funcao auxiliar para verificar se a funcao hash passada como argumento existe"""
    fhash = fhash.lower()
    if not fhash in nome_funcao: raise argparse.ArgumentTypeError("Funcao hash invalida. Disponiveis: " + ",".join(nome_funcao.keys()))
    return nome_funcao[fhash]



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



def caminhada(caminho, tamanho_grande):
    """Funcao auxiliar usada para percorrer o sistema de arquivos
    
    Args:
        caminho (str): caminho do arquivo ou diretorio de arquivos
        tamanho_grande (int) define o tamanho limite dos arquivos para processamento paralelo
    
    Returns:
        (arquivos_pequenos, indice_pequenos, arquivos_grandes, indice_grandes, nomes) (list, list, list, list, list): listas com os caminhos completos dos arquivos, seus indices e seus nomes
    """
    arquivos_pequenos, indice_pequenos, arquivos_grandes, indice_grandes, nomes = [], [], [], [], []
    for i in os.walk(caminho, topdown=False):
        for j in i[2]:
            nome = os.path.join(os.path.relpath(i[0], caminho), j).lstrip("\\./")
            caminho_completo = os.path.join(i[0], j)
            
            indice = len(arquivos_pequenos)+len(arquivos_grandes)
            if os.stat(caminho_completo).st_size > tamanho_grande:
                arquivos_grandes.append(caminho_completo)
                indice_grandes.append(indice)
            else:
                arquivos_pequenos.append(caminho_completo)
                indice_pequenos.append(indice)
            
            nomes.append(nome)
    return arquivos_pequenos, indice_pequenos, arquivos_grandes, indice_grandes, nomes



def processamento(arquivos_pequenos, indice_pequenos, arquivos_grandes, indice_grandes, functor, processos):
    """Funcao que gera lista com os hashes caclulados
    
    Args:
        arquivos_pequenos (list): lista de caminhos dos arquivos pequenos
        indice_pequenos (list): lista de indices dos arquivos pequenos
        arquivos_grandes (list): lista de caminhos dos arquivos grandes
        indice_grandes (list): lista de indices dos arquivos grandes
        functor (object / list): objeto callable  ou lista de objetos (usado para processar os arquivos)
        processos (int): numero de processos auxiliares para calcular os hashes de arquivos pequenos
    
    Returns:
        hashes (list): lista de hashes computados
    """
    hashes = [None]*(len(arquivos_pequenos)+len(arquivos_grandes))
    
    # processamento paralelo (loop para arquivos pequenos)
    if processos > 1:
        with multiprocessing.Pool(processos) as p:
            if not (type(functor) is list): resultado = p.map(functor, arquivos_pequenos)
            else: resultado = p.starmap(auxiliar, [(functor[indice_pequenos[i]], arquivos_pequenos[i]) for i in range(len(indice_pequenos))])
            for i in range(len(indice_pequenos)):
                hashes[indice_pequenos[i]] = resultado[i]
    
    # loop para arquivos grandes
    if not (type(functor) is list):
        for i in range(len(indice_grandes)): hashes[indice_grandes[i]] = functor(arquivos_grandes[i])
    else:
        for i in range(len(indice_grandes)): hashes[indice_grandes[i]] = functor[indice_grandes[i]](arquivos_grandes[i])
    
    return hashes



def cria_texto(caminho, functor, processos, tamanho_grande):
    """Funcao que gera o texto a ser gravado no arquivo de hashes
    
    Args:
        caminho (str): caminho do arquivo ou diretorio de arquivos
        functor (object): objeto callable (usado para processar os arquivos)
        processos (int): numero de processos auxiliares para calcular os hashes de arquivos pequenos
        tamanho_grande (int) define o tamanho limite dos arquivos para processamento paralelo
    
    Returns:
        texto (str): lista de hashes computados
    """
    texto = ""
    separador = funcao_separador[functor_funcao[functor]]
    
    # se o argumento -d for diretorio, calcula os hashes dos arquivos presentes
    if os.path.isdir(caminho):
        arquivos_pequenos, indice_pequenos, arquivos_grandes, indice_grandes, nomes = caminhada(caminho, tamanho_grande if processos > 1 else -1)
        hashes = processamento(arquivos_pequenos, indice_pequenos, arquivos_grandes, indice_grandes, functor, processos)
        texto = '\n'.join([hashes[i] + separador + nomes[i] for i in range(len(hashes))])
    
    # se o argumento -d for arquivo, calcula o hash apenas de um arquivo
    else:
        texto = functor(caminho) + separador + (os.path.basename(caminho) if os.path.isfile(caminho) else caminho) + "\n"
    
    return texto



def cria_arquivo_hashes(caminho, resultado, codificacao, functor, processos, tamanho_grande, impressao):
    """Funcao que salva o arquivo de hashes com a codificacao especificada pelo usuario
    
    Args:
        caminho (str): caminho do arquivo ou diretorio de arquivos
        resultado (str): caminho do arquivo de saida
        codificacao (str): codificacao do arquivo de saida
        functor (object): objeto callable (usado para processar os arquivos)
        processos (int): numero de processos auxiliares para calcular os hashes de arquivos pequenos
        tamanho_grande (int) define o tamanho limite dos arquivos para processamento paralelo
    """
    texto = cria_texto(caminho, functor, processos, tamanho_grande)
    if not len(texto): return
    
    if impressao:
        print(texto)
    else:
        try:
            with open(resultado, "w", encoding=codificacao) as f:
                f.write(texto)
                f.close()
        except Exception as e:
            print(e)
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', prefix='hashes.', delete=False) as f:
                f.write(texto)
                f.close()
                resultado = f.name
                print("Resultado escrito no arquivo: " + resultado)
        
        print(functor(resultado) + funcao_separador[functor_funcao[functor]] + resultado)



def checa_hashes(resultado, codificacao, functor, processos, tamanho_grande):
    """Funcao que verifica o arquivo de hashes com a codificacao especificada pelo usuario e imprime na tela o resultado
    
    Args:
        resultado (str): caminho do arquivo de hashes
        codificacao (str): codificacao do arquivo de hashes
        fhash (hashlib built-in function): funcao usada para o calculo do hash do arquivo de hashes
        processos (int): numero de processos auxiliares para calcular os hashes de arquivos pequenos
        tamanho_grande (int) define o tamanho limite dos arquivos para processamento paralelo
    """
    integros, diferentes = 0, 0
    nao_encontrados, encontrados = [], []
    contador_linha, erros = 0, 0
    arquivos_pequenos, indice_pequenos, arquivos_grandes, indice_grandes = [], [], [], []
    functors = []
    
    with open(resultado, encoding=codificacao) as arquivo_hashes:
        for linha in arquivo_hashes:
            contador_linha += 1
            separador = False
            for texto in separador_funcao:
                if texto in linha: separador = texto
            if not separador:
                erros += 1
                print("Erro ao ler a linha nº {}".format(contador_linha))
                continue
            
            hash_arquivo = linha.split(separador)
            if len(hash_arquivo) == 2:
                arquivo = os.path.join(os.getcwd(), hash_arquivo[1].rstrip("\n"))
                if not os.path.isfile(arquivo):
                    nao_encontrados.append(arquivo)
                else:
                    encontrados.append([hash_arquivo[0], arquivo])
                    posicao = len(arquivos_pequenos)+len(arquivos_grandes)
                    functors.append(funcao_functor[separador_funcao[separador]])
                    if os.stat(arquivo).st_size > tamanho_grande if processos > 1 else -1:
                        arquivos_grandes.append(arquivo)
                        indice_grandes.append(posicao)
                    else:
                        arquivos_pequenos.append(arquivo)
                        indice_pequenos.append(posicao)
            else:
                erros += 1
                print("Erro ao ler a linha nº {}".format(contador_linha))
        arquivo_hashes.close()
    
    hashes = processamento(arquivos_pequenos, indice_pequenos, arquivos_grandes, indice_grandes, functors, processos)
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
    
    parser = argparse.ArgumentParser(prog = 'pfsum', description = "Calcula hashes ou verifica um arquivo de hashes, versao " + pfsum_versao)
    parser.add_argument('-c', help = "Modo de verificacao de hashes", action='store_true')
    parser.add_argument('-p', help = "Impressao na tela", action='store_true')
    parser.add_argument('-d', metavar = "Entrada", help = "Arquivo ou diretorio de entrada (default = diretorio corrente, ou hashes.txt no diretorio corrente no modo de verificacao)", type = checa_caminho, default = diretorio_corrente)
    parser.add_argument('-o', metavar = "Saida", help = "Arquivo de saida do resultado (default = hashes.txt no mesmo diretorio de entrada)")
    parser.add_argument('-fh', metavar = "Funcao", help = "Funcao hash usada. Disponiveis: {} (default = sha256)".format(', '.join(nome_funcao.keys())), type = checa_funcao, default = "sha256")
    parser.add_argument('-e', metavar = "Codificacao", help = "Tipo de codificacao do arquivo de entrada/saida (default = utf8, lista completa em https://docs.python.org/3/library/codecs.html#standard-encodings)", choices = list(encodings.aliases.aliases.keys()), default = "utf8")
    parser.add_argument('-np', metavar = "Paralelismo", help = "Numero de processos usados para calcular hashes de arquivos pequenos (default = {})".format(int(os.cpu_count()/2)), type = int, default = int(os.cpu_count()/2))
    parser.add_argument('-s', metavar = "Tamanho", help = "Tamanho maximo de arquivo para processamento paralelo (default = 1G)", type = checa_tamanho, default = int(2**30))
    
    argumentos = parser.parse_args()
    
    if argumentos.np < 2: argumentos.np = 1
    if argumentos.s < 0: argumentos.s = 0
    
    # metodo de criacao do arquivo de hashes
    if not argumentos.c:
        resultado = os.path.abspath(argumentos.d)
        if os.path.isfile(argumentos.d): resultado = os.path.dirname(resultado)
        
        if argumentos.o: resultado = argumentos.o
        else: resultado = os.path.join(resultado, "hashes.txt")
        
        cria_arquivo_hashes(argumentos.d, resultado, argumentos.e, funcao_functor[argumentos.fh], argumentos.np, argumentos.s, argumentos.p)
    
    # modo de verificacao de hashes
    else:
        if argumentos.d == diretorio_corrente: argumentos.d = os.path.join(diretorio_corrente, "hashes.txt")
        
        if not os.path.isfile(argumentos.d): print('Arquivo de entrada "{}" invalido para o modo de verificacao'.format(argumentos.d))
        else:
            checa_hashes(os.path.abspath(argumentos.d), argumentos.e, funcao_functor[argumentos.fh], argumentos.np, argumentos.s)
