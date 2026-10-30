# Console SNCR

Uma interface local para testar a autenticação e as APIs de numeração do **Sistema Nacional de Controle de Receituários (SNCR)** da Anvisa, no ambiente de homologação.

Python com biblioteca padrão, HTML, CSS e JavaScript puro. Um container, sem banco de dados e sem dependências Python externas.

## Começar

É necessário ter **Docker com Docker Compose** e acesso a este repositório privado.

```sh
git clone https://github.com/alvarofln/sncr-console.git
cd sncr-console
docker compose up --build -d
```

Abra **[http://localhost:18767](http://localhost:18767)**. Não é necessário configurar domínio ou certificado.

1. Clique em **Entrar com gov.br** e conclua a autenticação no pop-up.
2. Após o retorno da Anvisa, o pop-up fecha e a tela principal mostra a validade da credencial.
3. Selecione o tipo de receita e informe conselho, UF e registro profissional.
4. Informe a quantidade ou o CNPJ, conforme o tipo, e clique em **Solicitar na Anvisa**.
5. Consulte o status HTTP, a resposta e o corpo enviado. Use **Baixar resultado JSON** para salvar o resultado.

O botão **Gerar**, ao lado do CNPJ, preenche um número aleatório com dígitos verificadores válidos para testes. Isso não comprova a existência ou regularidade de uma instituição.

Cada pessoa executa sua própria instância e autentica com sua conta gov.br. O login sozinho não solicita numerações.

## Operações disponíveis

| Tipos | Rota na Anvisa | Dados específicos |
| --- | --- | --- |
| NRA, NRB, NRB2 e NRR | `POST /numeracoes/notificacao-receita` | Quantidade de 10 a 50; depende de saldo do prescritor |
| RCE e RET | `POST /numeracoes/receita-especial-retencao` | CNPJ da instituição; bloco de 1.000 números |

Base utilizada: `https://api-gateway.hmg.apps.anvisa.gov.br/api-sncr/api/v1`.

A ferramenta usa exclusivamente homologação. Não emite nem assina prescrições e não envia o cabeçalho `X-Jws-Integradora`.

## Autenticação

O servidor prepara o endereço de retorno e abre `/auth/login?client_url=...` no pop-up. Ao receber o `session_id`, troca-o imediatamente por um token em `/auth/token`, com o `Origin` correspondente ao callback. A página principal consulta o estado da sessão e preserva os campos do formulário.

- O token fica somente na memória do servidor, separado por sessão do navegador.
- O token não é entregue ao JavaScript da página nem salvo em arquivos ou logs.
- A validade exibida vem do token recebido. Depois de expirar, faça login novamente.
- **Limpar credencial** remove o token local; a sessão gov.br do navegador permanece ativa.
- Reiniciar o container remove todas as sessões locais.

## Configuração

A porta padrão é `18767`. Para mudar, copie `.env.example` para `.env` e ajuste `SNCR_PORT`:

```dotenv
SNCR_PORT=18768
```

Depois execute `docker compose up -d` e abra `http://localhost:18768`. Use **localhost**, inclusive ao iniciar o login; o endereço de retorno é gerado a partir dessa configuração.

O Compose publica somente em `127.0.0.1`, executa o container sem privilégios e mantém o sistema de arquivos somente para leitura. Esta configuração se destina ao uso local.

## Comandos úteis

```sh
# Atualizar e reconstruir
git pull --ff-only
docker compose up --build -d

# Verificar o serviço
docker compose ps
docker compose logs --tail=50

# Parar e remover o container
docker compose down
```

## Dúvidas comuns

**O pop-up não abriu:** permita pop-ups para localhost e tente novamente.

**O login expirou:** clique em **Entrar novamente**. A sessão já existente no gov.br pode dispensar uma nova digitação de credenciais.

**A Anvisa informou falta de saldo:** o login pode estar correto; notificações dependem da disponibilidade de numerações para aquele prescritor e tipo.

**Houve timeout:** a operação pode ter sido processada pela Anvisa antes da falha de comunicação. O console não repete solicitações automaticamente.

**A porta está ocupada:** ajuste `SNCR_PORT` no `.env`.

## Desenvolvimento e testes

Com Python 3.12 ou superior:

```sh
python3 -m unittest -v
```

Os testes exercitam login, isolamento de sessão, expiração, validação de origem, os contratos das duas rotas e solicitações concorrentes, usando uma Anvisa simulada. Não fazem chamadas à Anvisa nem solicitam numerações.

Para executar sem Docker:

```sh
PORT=18767 python3 app.py
```

Estrutura:

```text
app.py           Servidor HTTP, sessão e integração com a Anvisa
static/          Interface e retorno do pop-up
test_app.py      Testes com servidor HTTP local e Anvisa simulada
Dockerfile       Imagem da aplicação
compose.yaml     Execução local e healthcheck
.env.example     Configuração opcional da porta
```

Referências: [documentação do SNCR](https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/controlados/sncr/documentos-do-sncr) e [Swagger de homologação](https://api-gateway.hmg.apps.anvisa.gov.br/api-sncr/swagger-ui/index.html).
