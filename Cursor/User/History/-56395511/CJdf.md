# Tarefas Concluídas

## Audio
- Responder com audio apenas quando o motorista especificamente pedir por isso ✅
- Interpretaçao de Audio ✅

## Arquivo
- Fazer o tratamento de arquivos (PDFs, imagens) ✅

## Transbordo
- Levantar um documento com parametros para quando será feito o transbordo ✅

## Fluxo Passivo
- Chamar API de cargas quando o usuário quiser ver novas ofertas ✅

## Configurações e Ajustes Gerais
- Configurar código dos action groups para refletir o que ele realmente deve fazer (Muitas funçoes que nao deveriam existir no código) ✅
- Chatbot pedindo informações desnecessárias ✅
- Interpretação de áudio ✅
- Ao responder mensagens especificas, passar o contexto em volta da conversa ✅
- Após implementaçao, envio de template terá um campo listaParametros, que será usado para enviar templates de forma paralela para os motoristas ✅
- Implementar para o proximo testes, esperar 5 segundos para responder o motorista -> Juntar todas as mensagens que chegarem ✅
  - Alterar para que o tempo seja adicionado toda vez (toda mensagem adiciona 5s)
- Quando o usuário demonstrar interesse em ver outras ofertas, não questione sobre o cadastro dele, apenas busque as ofertas normalmente e então, apenas se ele quiser realizar um embarque, faça o cadastro ✅

## Cadastros
- Dar preferencia aos parametros passados pelo chatbot nas funcoes de cadastro, pois o motorista deve ser capaz de alterar as informacoes ✅
- Antes de enviar os dados para o cadastro, verificar o formato para ver se é válido ✅
- Mandar so o primeiro nome do motorista e não o nome completo ✅
- Nao falar o cpf para o motorista, sempre perguntar o cpf dele. O mesmo vale para qualquer dado um pouco mais sigiloso ✅
  - (Alterar o session attributes e as instrucoes)
- Interpretaçao de coordenadas para que motoristas possam pedir ofertas proxima a localizacao deles ou para descarregar perto daquela regiao ✅
- Ao perguntar CPF/CNPJ do proprietário durante cadastro de veículo, sempre especificar "proprietário da ANTT" ✅
- Na verificação de cadastro intermitente, chatbot decide perguntar o nome quando deveria perguntar o CPF ✅

### Para confirmacao de cadastro fazer essas etapas aqui: ✅
1. O contato possui o telefone cadastrado no sistema, a gente faz uma confirmação pra ver se é ele mesmo e se não for, vamos ter que fazer um transbordo para o setor de cadastro para verificarem os dados dele. Se ele confirmar tudo (vamos ter que pensar melhor nesse fluxo de confirmação), continuamos com o cadastro de veículos.
2. O contato não possui o cadastro daquele numero de telefone no sistema, mas pelo CPF que ele nos passou vemos que já temos o cadastro dele. Nesse caso eu acho que também vai ter que ser feito o transbordo para a equipe de cadastro, porque o contato pode ser um golpista tentando se passar por um motorista já aprovado pela gente.
3. O contato não tem nem telefone e nem cpf cadastados, aí fazemos as etapas já esperadas.

## Action Groups
- Ajustar action groups para aceitar mais de uma forma de nome de parametros ✅
  - (Passar uma lista de possiveis nomes e ir atualizando com o tempo)
- Ajeitar parametros do action group de ofertas ✅
- Ajeitar a obtencao de parametros dos action groups ✅
- Configurar o parametro para funcao de flag de audio ✅

## Nomenclatura e Mensagens
- Chamar apenas de cavalo, nao cavalo mecanico ✅
- Novamente, chamar apenas de cavalo, nao de cavalo mecanico, ou trator ✅
- Mandar a mensagem inicial de template com uma explicacao melhor do que esta acontecendo ✅
- Passar a data atual no contexto inicial para o agente -> Instruir ele sobre essa informacao ✅
- Alterar instrucao para que o chatbot nao demonstrar intencao de fazer algo sem fazer isso ✅
- Na hora de perguntar sobre o telefone, perguntar sobre o telefone que ele esta usando no momento, se é para cadastro, ou se é o de uso diario, não é necessário mostrar o telefone ✅
- Adicionar informacoes padrao para contato com a Rodosafra. (Parametrizadas) ✅
- Quando uma possivel fraude for detectada (problema no cadastro) nao falar que é por questao de segurança, so falar que teve um problema no cadastro e que vai passar pra alguem verificar isso ✅
- Passar nas instrucões abreviacoes comuns de palavras para que o chatbot possa entender ✅
- Melhorar as mensagens enviadas em caso de fraude ✅
- Adicionar novamente instrucoes de formatacao de numero, CPF, placa, etc ✅
- Especificar para o chatbot que ele respondera mensagens no whatsapp. Entao pode usar emojis e coisas do tipo ✅
- A não ser que a pessoa peça especificamente as informacoes de contato da rodosafra, apenas faca transbordo, nao indique ela a entrar em contato por conta propria ✅
- Melhorar as instrucoes para, quando o chatbot pedir o cavalo e a pessoa passar a placa de um equipamento, se for encontrado informacoes no cadastro, perguntar algo do tipo: "Encontrei aqui seu conjunto, a placa "XXX" se refere a um equipamento, o seu cavalo é a placa "XXY"? ✅
- Caso o cadastro esteja todo de acordo, não é necessário confirmar o nome e o numero da pessoa. ✅
- Começar a passar o dia da semana em que estamos no contexto inicial (Envia template) ✅
- Nao falar para o usuário o nome dele, sempre perguntar. ✅
- Alterar linha na instrucao de cadastro que pode estar confundindo a IA sobre perguntar o nome ✅
- Quando a pessoa perguntar como o bot conseguiu o numero dela, se a verificacao foi feita, se ele ja carregou antes com a Rodsafra fale que esse é o motivo, se nao transbordo. Ou que ele nao lembra de realizar cadastro, fazer transbordo ✅
- Bot no embarque está cadastrando a data correta, porem falou a data errada ✅
- Ainda está perguntando o nome da pessoa, reforçar esse ponto no agente supervisor tambem. ✅
- Chatbot nunca deve usar "Daqui a pouco te retorno" em situações que não são transbordo - frase exclusiva para transbordo ✅
- Chatbot deve sempre induzir motorista a enviar resposta, não deixar resposta "finalizada" se há próximos passos no fluxo ✅
- Nunca expor termos técnicos (CENÁRIO, cadastrado_telefone, status, session) ao motorista ✅
- Nunca calcular ou mostrar valor final que motorista vai receber - apenas informar valor bruto e mencionar descontos ✅

## Guardrails e Contexto
- Adicionar guardrail para que o chatbot nao saia do contexto de ofertas ✅

## Erros e Tratamento
- Sempre que ocorrer um erro no processessamento do chatbot, responder o motorista de qualquer forma, nao deixe ele sem resposta ✅
- Implementar um retry para determinados tipos de erro, apos o numero determinado de retries, sinalizar a Rodosafra se o sistema persistir ✅
- Quando ocorrer um timeout em alguma chamada de API, tentar novamente, até 3 vezes ✅
- Quando começar a dar muito erro nas chamadas de api, fazer transbordo (Será feito quando a logica do transbordo estivem bem definida) ✅
- Embarque erro 403 (Autorizaçao) ✅
- Verificar (envia-template): [API-WARNING] Erro ao processar resposta: Expecting value: line 1 column 1 (char 0), mas requisição bem-sucedida ✅

## Token e Autenticação
- Mudar o codigo do servidor para sempre buscar o token no parameter store e nao guardar em chache por mais de 5 min. ✅

## API de Cargas
- Trabalhar na nova api de cargas (busca ofertas) ✅
- tipo_pagamento_frete -> Nova variavel para rota de ofertas
  - valores: "tonelada" e "fixo" ✅
- Mandar todas as ofertas de uma vez so (Fazer isso após aumento das cotas) ✅
- Quando o motorista nao falar o periodo em que ele quer buscar ofertas passar um valor default (entre hoje e depois de amanhã) ✅
- Ajeitar erro de leitura dos IDs na busca de ofertas ✅

## Negociação
- Nao mandar template para motorista que ja esta negociando/conversando. Criar um sistema de negociacao ✅
  - Criar um parametro para determinar quanto tempo esperar para mandar mensagem novamente para o motorista
- Caso o motorista esteja insistindo muito na mudança de preco, ou parecer que ele esta desistindo da oferta, fazer transbordo ✅
- Nao enviar templates com negociacao ativa ✅

## Verificações e Validações
- Se a pessoa já está cadastrada (cpf e telefone), não é necessário perguntar o cpf novamente, apenas continuar o processo para o cadastro/verificacao de veiculo ✅
  - Caso ela corrija o cpf, validar com o que voce ja possui e falar com ela "o que temos aqui é diferente, tem certeza que é esse ai? (nunca mostrar o cpf que temos)" e caso ele erre 3 vezes, transbordo
- Quano um cadastro com o numero e cpf for encontrado, mas o nome for diferente, fazer transbordo para o setor de cadastro ✅
- Se a pessoa deseja mudar de numero, fazer transbordo para o setor de cadastro ✅
- A IA nao pode atualizar numero de telefone, se uma situaçao acontecer que os numeros sejam diferentes, fazer transbordo ✅
- No action group de verificação de cadastro, fazer verificação de fraude (CPF com número diferente = transbordo) ✅
- Na verificação de CPF, se motorista errar 1 vez, perguntar se quer corrigir e verificar novamente; se errar 2 vezes, fazer transbordo ✅

## Veículos
- Ao puxar o cadastro de uma placa, perguntar sobre o conjunto todo de uma vez ✅
  - O mesmo vale para o motorista, quando pegar todas as informacoes, ja passar tudo para o chatbot de uma vez
- Nao passar lista de veiculo, apenas perguntar "qual seu tipo de cavalo ou caminhao?" ✅
- No lugar de perguntar, por exemplo, que tipo de carreta, perguntar qual o tipo de conjunto ✅
- Na hora de buscar ofertas, o id to tipo de veiculo passado é o do conjunto, entao é bom perguntar o conjunto de uma vez. ✅
  - Caso ele tenha algum equipamento e ele comentar a gente passa isso tambem, se ele comentar so o conjunto, e for um conjunto que possui equipamento obrigatoriamente, perguntar o equipamento tambem
- Salvar os veiculos encontrados e associar ao motorista que esta conversando (Necessario para passar equipamentos no embarque) ✅
- Ajeitar salvamento de equipamentos ✅
- Ajustar salvamento do veiculo ✅
- Total de equipamentos nao esta sendo salvo na tabela de veiculos ✅
- Pegar os equipamentos primariamente das tabelas (ID motorista->Id veiculo->Ids equipamentos[IDs listados no item do veiculo]) ✅
- Para verificao de veiculo válido caso tenha equipamento, pegar o tipo de veiculo do primeiro equipamento e o tipo de equipamento do primeiro equipamento. Quando não existe equipamento, é o tipo do cavalo que é verificado ✅
- Nao perguntar qual o tipo de conjunto do motorista (Perguntar o se é cavalo ou caminha, se for cavalo que tipo de cavalo que é) ✅
- Na verificação de veículo, buscar também o tipo/ID do veículo/equipamento para melhor contexto do chatbot ✅
- Na herança de dados da negociação passada, puxar campos de veículo também (equipamento_ids, veiculo_cavalo, veiculo_cavalo_id) ✅
- Action group de cadastro de veículo retornando erro no índice id_veiculo-index ✅
- Implementar verificação de compatibilidade veículo-carga antes do embarque (função `verificar_compatibilidade_veiculo_carga`) ✅

## Embarque
- Ajeitar os parametros utilizados pela funcao de embarque ✅
- Alterar como o embarque pega o ID de veiculo ✅
- Adicionar para buscar o id da carga no banco de dados (item do proprio motorista na tabela de motoristas) ✅
- Embarque ta fazendo a busca do numero sem o 55 ainda ✅
- Ajustar a query de busca para buscar o carga_id na tabela (embarque-action-group) ✅
- Nao perguntou data nem peso que quero carregar, reforçar isso na instruçao de embarque ✅
- Verificar o tipo de veiculo permitido para aquela carga antes de fazer o embarque ✅
- Checar o periodo de disponibilidade da carga tambem antes de realizar o embarque (inicio_periodo e fim_periodo) ✅
- Sempre solicitar a hora ou o turno [pela manha (8:00), pela tarde (15:00)] na hora de realizar o embarque ✅
- Se criou um embarque e retornou 200/201 nao repetir esse processo novamente, pois o embarque será duplicado caso isso ocorra ✅
- Orientar o chatbot a nao perguntar sobre o horario em que ele quer embarcar (pela manha ou pela tarde) sem antes perguntar de forma vaga (quando voce quer embarcar?) ✅
- No processo de embarque, chatbot cadastrando data certa mas falando data errada - retornar payload do action group para o chatbot ✅

## Ofertas Urgentes
- Descobrir como tratar ofertas urgentes (Em primeiro momento apenas enviar-las independente da situação) ✅
- Mesmo sendo carga urgente não é para mandar se uma negociacao estiver ativa ✅

## KPIs ✅
### Processo Ativo
1. Quantas cargas estamos ofertando por dia
2. Quantos motoristas foram contactados por dia
3. Dos motoristas cotactados, quantos responderam
4. Dos que responderam, quantos deu match e fez o Embarque
5. Qual a média de carga ofertado por motorista
6. Qual o humor do motorista durante a conversa no chat (Inviável no momento)
7. Quantas conversas foram transferidas

### Processo Passivo
1. Quantos motoristas chamaram a gente
2. Desses motoristas, quantos o chat atendeu
3. Desses atendidos, quantos converteram em Embarque
4. Qual o humor do motorista durante a conversa no chat (Inviável no momento)

## Fluxo de Transbordo
- Fluxo de transbordo ✅
- Nas KPIs, alem de salvar se foi feito o transbordo, salvar o porque do transbordo tambem (Fazer quando o transbordo estiver implementado) ✅

## Memória e Sessão
- Investigar problema na hora da criacao de session_id, alguns estao sendo criados sem o timestamp, que acaba gerando uma brecha de contexto ✅
- Adicionar controle da memoria do agente ✅
- Tempo de memoria ainda esta no metodo antigo de timestamp ✅
- Passar timestamp em ISO para criar sessao e memoria ✅
- Verificar possivel problema de contexto na mudança de hora ✅
- Na tabela de negociação, campo veiculos_updated_at e api_errors.timestamp_unix não estavam em formato ISO ✅

## WebSocket
- Servidor websocket esta perdendo conexao, mudar para ele sempre tentar conectar no websocket, mesmo quando o codigo constar que ainda está conectado ✅
- Alterar para pegar o IP publico do ECS dinamicamente ✅
- Alterar manuseio local do websocket: após 1h de conversa, desconectar do websocket e ouvir SSE; se houver novo evento, reconectar ao websocket ✅

## Cadastro em Duas Etapas
Vamos separar o sistema de cadastro em duas etapas, um cadastro parcial e um cadastro completo. Apos fazer a chamada inicial para a rota parcial, se ela retornar sucesso, chamar a rota de cadastro completo (nao precisa se preocupar com o retorno da rota completa) ✅

## Novos Campos e Validações
- Ainda está verificando o nome completo ✅
- Trocar o id_oferta na tabela de ofertas ✅
- Criar fallback do envio de contexto, buscar informacoes da oferta no dynamodb ✅
- Dois campos novos (ofertas-disponiveis):
  - "peso_total_programado": "100" -> Peso disponivel programado por dia em tonelada
  - "tipo_programacao": "fixa" -> LIMITE MÁXIMO - motorista pode carregar menos, mas não pode ultrapassar; "livre" -> pode ultrapassar o valor programado ✅
- Mudar a tabela de negociacao para suportar a busca de ofertas atreladas a aquela conversa, adicionando a sort key (ainda a determinar) para que possa fazer uma conexao com a carga utilizada. ✅
- Começar a mandar os novos campos para o contexto (envia-template) ✅
  - Implementado: peso_total_programado e tipo_programacao agora são extraídos da oferta e incluídos nos session_attributes
  - Contexto do chatbot agora explica claramente: "fixa" = LIMITE MÁXIMO (pode carregar menos, mas NÃO pode ultrapassar); "livre" = PODE ULTRAPASSAR (valor é apenas referência) ✅
- Mudar envio de contexto de programação fixa/livre: fixa limita até 100t mas pode carregar menos; livre pode passar do peso definido ✅

---

# Tarefas Pendentes

## Transbordo
- Caso o usuário tenha dúvidas sobre o embarque durante a conversa, "marcar" aquele motorista para, no final da conversa, depois de fazer o embarque, chamar o transbordo ✅

## Dados e Configurações
- Mudar a lista de tipo de veículos e equipamentos para um banco de dados ✅

- Mudar 

---

# Pendências Rodosafra

## Instruções de Veículos
Instruir o chatbot de quais as possiveis formas que uma pessoa pode chamar um determinado veiculo (Pendencias Rodosafra)

## Documentação
Documentaçao de status do servidor de websocket (Pendencia rodosafra)

## Valores e Descontos
Voce nunca deve falar para o motorista quanto que ele vai ganhar naquela corrida, mesmo que ele pergunte especificamente isso.

Por exemplo, no caso de ser um valor por tonelada e o motorista perguntar.

Pois existem descontos de impostos, forma de pagamento, quebra e varios outros motivos, então nao comente esses valores. Apenas fale algo do tipo: Valor bruto é tal, mas existem descontos por tal coisa e tal coisa.

Rodosafra ira mandar um texto padrao para enviar para os motoristas porsteriormente (Pendencia Rodosafra)

### Alguns valores a serem adicionados:
- Valor de desconto da seguradora (90,00)
- Valor pago no VPO (pago na tag)
- Valor de desconto de quebra (somente após a descarga)
- Valor de desconto no adiantamento ou saldo (autônomo, desconta o IR) [Desconto para todo mundo, porem especificamente para motorista com CPF é descontado no IR]

## Erros de Verificação
Algumas APIs estao retornando erro 500 sem motivo aparente (Pendencia Rodosafra)

## 
Campo CPF pode vir nulo, neste caso devemos verificar o cadastro apenas pelo telefone (nao verificar pelo cpf para nao acusar fraude), a partir do que veio do telefone, construimos os dados

---

# Informações Gerais

## Volume de Operações
- 400 ofertas de 5 a 10 motoristas
- 4000 mensagens diarias

## Opções para Visualização de KPIs
- **Quicksight (BI Dashboards)** → Opção mais cara, porém mais avançada/visualmente atrativa
- **Arquivos em texto + Imagens** → Opção mais simples, porem com custo quase nulo

# Processos Implementados

## Fluxo ativo (Envio de template)

 - Funcionalidade macro pronta
 - Processo já testado e funcional

## Cadastro

 - Funcionalidade macro pronta
 - Processo já testado e vários erros já corrigidos
 - Pequenos detalhes podem ser encontrados nos testes, porém está funcional

## Embarque

 - Funcionalidade macro pronta
 - Processo já testado e vários erros já corrigidos
 - Pequenos detalhes podem ser encontrados nos testes, porém está funcional

## Busca de Ofertas 
 
 - Funcionalidade macro pronta
 - Processo ainda não foi testado, apenas internamente

## Transbordo
 
 - Funcionalidade macro pronta
 - Processo desativado para os testes
 - Ainda não testado

## Fluxo Passivo (Busca de conversas para serem respondidas)

 - Funcionalidade macro pronta
 - Ainda não testado
 - Pendência Rodosafra (Ambiente de Staging do serviço de chat)