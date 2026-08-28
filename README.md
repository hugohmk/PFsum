# PFsum
Python File Checksum (PFsum) é um script em Python usado para calcular códigos de autenticidade de arquivos, inspirado no programa Fsum (SlavaSoft). Suporta os seguintes algoritmos:  md5, sha1, sha256, sha512, crc32 e adler32. É necessário ter o Python versão 3.11 ou superior instalado.

Foi criado com o objetivo de facilitar a geração de lista com códigos de autenticidade de múltiplos arquivos, principalmente com relação ao tempo de processamento e à codificação do arquivo de saída.

## Diferenciais
- Compatível com arquivos de hashes gerados pelo Fsum (SlavaSoft)
- Controle de codificação do arquivo de hashes de saída/entrada
- Processamento paralelo configurável, com uso de duas filas: uma para arquivos pequenos, e outra para arquivos grandes
- Leitura antecipada nos arquivos grandes, sobrepondo a leitura do disco ao cálculo do hash
- Barra de progresso com percentual, velocidade e estimativa de tempo restante
- Ordem de saída estável: as linhas seguem a varredura do diretório, e não a ordem em que os cálculos terminam, então mudar "-np" ou "-s" não muda o arquivo gerado
- Código aberto e relativamente pequeno

## Uso
Ao chamar o script, um arquivo com nome "hashes.txt" será criado no diretório corrente. Nele estarão presentes os códigos de autenticidade SHA256 dos arquivos listados no diretório corrente e em subdiretórios, listados recursivamente. No final do processamento, será impresso no console o código de autenticidade do próprio arquivo "hashes.txt".
```
python "CAMINHO_COMPLETO\pfsum.py"
```

Se já existir um arquivo de saída com o mesmo nome, o script pergunta antes de sobrescrevê-lo, e a pergunta vem **antes** do processamento — não há risco de descobrir no fim de uma árvore grande que o arquivo não devia ser trocado. Responder "s" (ou "sim") grava; qualquer outra resposta cancela e não altera nada. A pergunta só aparece quando há um terminal para respondê-la: com a entrada padrão redirecionada, como em uma tarefa agendada, o arquivo é sobrescrito como sempre foi. Para nunca perguntar, usar "-sp".

Enquanto o processamento acontece é desenhada uma barra de progresso, com o percentual concluído, a velocidade de leitura e a estimativa de tempo restante. Ela é redesenhada de tempos em tempos (e não a cada arquivo), e sai na saída de erro: redirecionar a saída normal, como em "-p > hashes.txt", continua funcionando. A barra só aparece quando a saída de erro é um terminal, e pode ser desligada com "-sb".

Para verificar um arquivo "hashes.txt", abrir um console no mesmo diretório do arquivo e chamar o script no modo de verificação "-c".
```
python "CAMINHO_COMPLETO\pfsum.py" -c
```

## Opções de linha de comando
| Comando  | Descrição |
| ------------- |:-------------:|
| -h, --help     | Mostra os comandos do script    |
| -c     | Modo de verificação de hashes     |
| -p     | Impressão na tela (não cria o arquivo "hashes.txt")     |
| -d     | Arquivo ou diretório de entrada     |
| -o    | Arquivo de saída do resultado     |
| -fh    | Função hash usada. Disponíveis: md5, sha1, sha256, sha512, crc32, adler32 (default = sha256)     |
| -e    | Tipo de codificação do arquivo de entrada/saída (default = utf8; aceita qualquer nome que o Python reconheça, como utf-8, cp1252, latin1 ou ascii)     |
| -np    | Número de tarefas usadas para calcular hashes de arquivos pequenos (default = metade do nº de processadores, máximo = 256)     |
| -s    | Tamanho máximo de arquivo para processamento paralelo (default = 1G)     |
| -sb    | Não desenha a barra de progresso     |
| -sp    | Não pergunta: sobrescreve o arquivo de saída existente sem pedir confirmação     |


## Exemplos
Criar um arquivo com nome "checksum.txt", no diretório corrente, usando o algoritmo md5:
```
python "CAMINHO_COMPLETO\pfsum.py" -o .\checksum.txt -fh md5
```

Imprimir na tela o hash SHA512 de um arquivo:
```
python "CAMINHO_COMPLETO\pfsum.py" -o -p -fh sha512 -d "CAMINHO_ARQUIVO"
```

Processar sempre em paralelo (recomendado em SSD, quando há muitos arquivos acima do limite de "-s"):
```
python "CAMINHO_COMPLETO\pfsum.py" -s 10T
```

Nunca processar em paralelo, ou seja, um arquivo por vez (nos arquivos grandes a leitura continua sendo antecipada):
```
python "CAMINHO_COMPLETO\pfsum.py" -s 0
ou
python "CAMINHO_COMPLETO\pfsum.py" -np 1
```

Criar um arquivo "hashes.txt" de forma semelhante ao Fsum (redirecionando a saída para um diretório superior):
```
python "CAMINHO_COMPLETO\pfsum.py" -p > ..\hashes.txt
```

Processar sem a barra de progresso:
```
python "CAMINHO_COMPLETO\pfsum.py" -sb
```

Regerar o arquivo de hashes sem que a confirmação de sobrescrita apareça:
```
python "CAMINHO_COMPLETO\pfsum.py" -sp
```