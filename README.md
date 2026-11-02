# DrSaygon Video Factory 6.0.2

Aplicativo local-first da Saygonsoft para transformar filmagens em um corte editado, com foco em **conteúdo primeiro**, continuidade natural, áudio sob controle e saídas adequadas tanto para finalização no Adobe Premiere quanto para publicação direta.

---

## Instalação e Execução

### Pré-requisitos
- **Sistema Operacional:** Windows 10 ou 11 (64 bits).
- **Python:** Versão 3.11, 3.12 ou 3.13 (disponível via `py` launcher ou adicionado ao PATH).
- **Chave Gemini (opcional para áudio original, recomendada):** Gratuita em [Google AI Studio](https://aistudio.google.com/apikey).
- **FFmpeg:** Opcional no sistema. Se não estiver no PATH, o instalador baixa automaticamente uma versão portátil e autônoma.

### Como instalar a partir de um clone limpo

1. **Clone o repositório:**
   ```bash
   git clone https://github.com/drsaygon/drsaygon-video-factory.git
   cd drsaygon-video-factory
   ```

2. **Execute o instalador:**
   ```cmd
   INSTALAR.bat
   ```
   *O instalador cria automaticamente os ambientes virtuais `.venv` e `.venv_voice`, instala as dependências, configura o FFmpeg portátil se necessário e convida a registrar sua chave Gemini via `CONFIGURAR_CHAVE.bat`.*

3. **Verifique o diagnóstico:**
   ```cmd
   DIAGNOSTICO.bat
   ```

4. **Inicie o programa:**
   ```cmd
   INICIAR.bat
   ```
   *O navegador abrirá automaticamente em `http://127.0.0.1:8765`.*

---

## Filosofia da v6

A v6 abandona a ideia de “cada frase = uma nova cena”. O motor trabalha com duas camadas independentes:

1. **Evidência semântica** — confirma o que precisa aparecer.
2. **Scene Continuity Engine** — decide onde um editor humano realmente cortaria.

Em vídeos longos, o modo automático favorece tomadas sustentadas e tenta ficar em torno de um ritmo calmo. Ações concretas continuam podendo receber cortes precisos, mas explicações, demonstrações, conversas, unboxings, passeios e testes não são fragmentados só porque a narração mudou de frase.

## Modos de áudio

### Narração
A narração gravada/aprovada é a trilha principal. O áudio original fica **mutado por padrão**, mas pode ser habilitado individualmente em qualquer tomada na etapa 6.

### Áudio original
Ideal para vlog, canal infantil, eventos, entrevistas, viagens, vídeos familiares e cenas espontâneas. A etapa de Narração deixa de ser obrigatória. As cenas entram inicialmente com o áudio da câmera em destaque; cada tomada pode depois ser mutada ou reduzida.

### Misto
Narração + áudio original configurável por tomada:
- `mute`: sem áudio original;
- `ambient`: áudio original baixo;
- `feature`: áudio original em destaque. Quando há narração, ela é automaticamente reduzida nesse intervalo.

## Gravação manual sem cortar voz

A gravação manual é tratada como material autoral e nunca passa por `silenceremove` nas bordas.

Além disso, a interface v6:
- começa a capturar antes de exibir **FALE AGORA** (pré-roll);
- mantém cerca de meio segundo de pós-roll após o clique em Parar;
- preserva integralmente o arquivo capturado;
- aplica apenas filtragem leve e normalização de nível.

Isso protege plosivas, respirações, início de palavra e sílaba final.

## Scene Continuity Engine — vídeo longo

Para `youtube_16_9`, o modo automático passa a usar política conservadora:
- frases contextuais/abstratas são consolidadas em tomadas fortes;
- tomadas menores que ~4,5 s geram alerta/reparo quando aparecem em excesso;
- o alvo automático é muito mais calmo que Shorts;
- A-B-A, replay artificial, overlap forte e reset de timestamp passam por autocorreção;
- o Quality Guard não devolve ao usuário um problema que o próprio editor automático consegue corrigir.

## Saídas de vídeo

### Master para Premiere
`MASTER_DRSAYGON_VIDEO_FACTORY.mp4`

O Master tem chave própria e um seletor que só contém opções de Master:
- **Master HQ (recomendado):** ~50 Mb/s até 30 fps / ~70 Mb/s acima de 30 fps;
- **Master Máxima:** ~80 Mb/s até 30 fps / ~100 Mb/s acima de 30 fps;
- H.264 High, AAC 384 kb/s / 48 kHz em ambos.

Objetivo: preservar margem de qualidade para nova finalização no Premiere.

### YouTube 1080p HQ
`CONTEUDO_PRINCIPAL.mp4`
- ~12 Mb/s em até 30 fps
- ~18 Mb/s acima de 30 fps
- AAC 384 kb/s

### Social 1080p HQ
`CONTEUDO_PRINCIPAL.mp4`
- ~10/14 Mb/s
- AAC 256 kb/s

Na etapa 6, Master e publicação são **saídas independentes**. Cada uma tem seu próprio switch e seu próprio seletor contextual. É impossível salvar/renderizar uma configuração sem nenhuma saída ativa.

## Metadados “DrSaygon Video Factory”

Os MP4s finais recebem, quando o container suporta, metadados explícitos como:
- `software=DrSaygon Video Factory`
- `writing_application=DrSaygon Video Factory`
- `encoded_by=DrSaygon Video Factory`
- `tool=DrSaygon Video Factory`
- `handler_name=DrSaygon Video Factory`
- `encoder=DrSaygon Video Factory` no stream de vídeo

Observação técnica: o muxer MP4 do FFmpeg mantém também sua identificação interna `Lavf`, e plataformas como YouTube podem usar heurísticas próprias ou ignorar tags. O Video Factory grava a identificação honesta mais forte disponível sem falsificar internals do container.

## Fluxo rápido

1. Criar projeto.
2. Escolher áudio: Narração / Original / Misto.
3. Colocar filmagens em `01_FILMAGENS`.
4. Registrar em **O que só você sabe** qualquer fato que a IA não possa deduzir.
5. Analisar.
6. Gerar/ajustar roteiro.
7. Se houver narração, gravar/aprovar voz; em Áudio Original, pular essa etapa.
8. Preparar corte profissional.
9. Opcionalmente ajustar áudio de qualquer tomada.
10. Gerar Master + entrega.
11. Publicar/finalizar no Premiere.

## Testes de regressão incluídos

- bordas de gravação manual preservadas;
- pré/pós-roll da UI verificados por inspeção de release;
- áudio original sem dependência de narração;
- áudio misto + ducking;
- áudio original configurável por cena inclusive em projetos narrados;
- ritmo long-form;
- Continuity Engine;
- Quality AutoRepair / Source Lane;
- Master HQ/Máxima + YouTube/Social + metadados;
- migração segura de projetos v6.0.0;
- arquivamento conjunto de Master e publicação para impedir arquivo obsoleto;
- Export Studio testado em Chromium: toggles, combos contextuais e layout responsivo;
- duração de projeto 1–90 minutos;
- Publication Engine genérico.

Execute `TESTE_LOCAL.bat` em uma instalação configurada.

## 6.0.2 — confiabilidade de áudio e Evidence Lab

- O Evidence Lab agora toca um proxy H.264/AAC compatível com navegador, com seek por HTTP Range, em vez de depender do codec original da câmera.
- A gravação manual usa **Smart Edge Trim**: remove somente silêncio confirmado nas bordas, preserva margens de segurança da fala e nunca encurta pausas internas.
- O tipo **Fala / áudio importante** pode ser marcado manualmente para proteger áudio original em evidências específicas.

