"""
Terminal de Tarefas — janela translúcida, sem bordas, com cara e funcionalidade de terminal.

- Só fecha pelo botão ✕ do menu no canto superior direito (Alt+F4 e `exit` são ignorados).
- Menu com 3 botões: ◉ fixar no topo | ◐ transparência | ✕ fechar.
- Terminal de verdade: `cd`, `cd ..`, `cd ~`, `cd -`, `D:` (Windows), Tab completa caminhos,
  setas ↑/↓ navegam no histórico, Ctrl+C interrompe o comando em execução,
  e qualquer outro comando (git, python, npm, dir, ls...) roda na pasta atual.
- Painel de tarefas no topo (a divisória pode ser arrastada):
  botões ✎ editar / ✓ feita / ✕ remover / ⌫ limpar feitas / ➕ adicionar, duplo clique = concluir,
  Delete = remover, clique direito = remover.
- Também dá para gerenciar tarefas pelo terminal:
      todo                lista
      todo add <texto>       cria
      todo edit <n> <texto>  edita (sem <texto>: abre no prompt para você ajustar)
      todo done <n...>       marca como feita (só quando VOCÊ mandar)
      todo undone <n...>     reabre
      todo rm <n...>         remove (aceita 1 3 5 ou 2-4)
      todo clear             remove as concluídas
- Tarefas salvas em tarefas.json (mesma pasta do script).

Uso: python terminal_tarefas.py   (Python 3 com tkinter, nada para instalar)
"""

import getpass
import json
import os
import queue
import re
import signal
import socket
import subprocess
import threading
import ctypes
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk

ARQUIVO = Path(__file__).with_name("tarefas.json")
WIN = os.name == "nt"
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Cores
BG = "#0c0c14"
PAINEL = "#12121c"
BARRA = "#08080e"
SELECAO = "#2a2a3d"
FG = "#cdd6f4"
BRANCO = "#ffffff"
MUDO = "#6c7086"
VERDE = "#a6e3a1"
AZUL = "#89b4fa"
VERMELHO = "#f38ba8"
AMARELO = "#f9e2af"


def codificacao_saida():
    if WIN:
        try:
            return "cp%d" % ctypes.windll.kernel32.GetOEMCP()
        except Exception:
            return "cp850"
    return "utf-8"


def escolher_fonte(root):
    disponiveis = set(tkfont.families(root))
    for nome in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono",
                 "Liberation Mono", "Courier New"):
        if nome in disponiveis:
            return nome
    return "Courier"


class App:
    def __init__(self):
        self.root = r = tk.Tk()
        r.title("Terminal de Tarefas")
        r.overrideredirect(True)                 # sem barra nativa (sem X do sistema)
        r.geometry("720x540+100+100")
        r.minsize(460, 360)
        r.configure(bg=BARRA)

        self.niveis = [0.95, 0.85, 0.70, 0.55]   # níveis de opacidade
        self.nivel = 1
        r.attributes("-alpha", self.niveis[self.nivel])

        self.topo = True
        r.attributes("-topmost", True)

        # Ignora qualquer tentativa de fechar fora do botão ✕ (ex.: Alt+F4)
        r.protocol("WM_DELETE_WINDOW", lambda: None)

        self.fonte = escolher_fonte(r)
        self.tam = 10
        self.enc = codificacao_saida()
        try:
            self.usuario = getpass.getuser()
        except Exception:
            self.usuario = "user"
        self.host = socket.gethostname().split(".")[0]

        self.cwd = Path.home()
        self.anterior = self.cwd
        self.historico = []
        self.pos_hist = 0
        self.proc = None
        self.ocupado = False
        self.fila = queue.Queue()
        self.tarefas = self.carregar()

        self.montar()
        self.atualizar_tarefas()
        self.banner()
        self.atualizar_prompt()
        r.after(40, self.drenar_fila)
        r.after(150, self.entrada.focus_force)

    # ------------------------------------------------------------------ dados
    def carregar(self):
        try:
            dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
            return [{"texto": str(t["texto"]), 
                     "feita": bool(t.get("feita", False)),
                     "prioridade": str(t.get("prioridade", "Baixa")),
                     "data": str(t.get("data", ""))}
                    for t in dados]
        except Exception:
            return []

    def salvar(self):
        try:
            ARQUIVO.write_text(json.dumps(self.tarefas, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        except Exception as e:
            print("Erro ao salvar:", e)

    # -------------------------------------------------------------- interface
    def botao(self, pai, texto, comando, cor=FG, bg=BARRA, **kw):
        b = tk.Label(pai, text=texto, bg=bg, fg=cor, cursor="hand2",
                     font=(self.fonte, self.tam), **kw)
        b.bind("<Button-1>", lambda e: comando())
        b.bind("<Enter>", lambda e: b.config(bg=SELECAO))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    def montar(self):
        r = self.root

        # ---- Barra superior: arrastar + menu de 3 botões à direita
        barra = tk.Frame(r, bg=BARRA, height=32)
        barra.pack(fill="x")
        barra.pack_propagate(False)

        titulo = tk.Label(barra, text="  ▍terminal de tarefas", bg=BARRA, fg=MUDO,
                          font=(self.fonte, 10, "bold"), anchor="w")
        titulo.pack(side="left", fill="both", expand=True)
        for w in (barra, titulo):
            w.bind("<ButtonPress-1>", self.inicio_arrasto)
            w.bind("<B1-Motion>", self.arrastar)

        self.botao(barra, "✕", self.fechar, VERMELHO, BARRA, width=4).pack(side="right", fill="y")
        self.botao(barra, "◐", self.trocar_opacidade, FG, BARRA, width=4).pack(side="right", fill="y")
        self.b_topo = self.botao(barra, "◉", self.alternar_topo, AZUL, BARRA, width=4)
        self.b_topo.pack(side="right", fill="y")

        # ---- Corpo: painel de tarefas (cima) + terminal (baixo)
        paned = tk.PanedWindow(r, orient="vertical", sashwidth=5, bg=BARRA,
                               bd=0, sashrelief="flat", opaqueresize=True)
        paned.pack(fill="both", expand=True)

        # Painel de tarefas
        painel = tk.Frame(paned, bg=PAINEL)
        cab = tk.Frame(painel, bg=PAINEL)
        cab.pack(fill="x")
        tk.Label(cab, text=" TAREFAS", bg=PAINEL, fg=MUDO,
                 font=(self.fonte, 9, "bold")).pack(side="left", pady=(4, 2))
        self.info = tk.Label(cab, text="", bg=PAINEL, fg=MUDO, font=(self.fonte, 9))
        self.info.pack(side="right", padx=8)

        corpo = tk.Frame(painel, bg=PAINEL)
        corpo.pack(fill="both", expand=True, padx=6)
        self.lista = tk.Listbox(
            corpo, bg=PAINEL, fg=FG, selectbackground=SELECAO, selectforeground=BRANCO,
            activestyle="none", relief="flat", highlightthickness=0, borderwidth=0,
            exportselection=False, font=(self.fonte, self.tam))
        rolagem = tk.Scrollbar(corpo, command=self.lista.yview)
        self.lista.config(yscrollcommand=rolagem.set)
        rolagem.pack(side="right", fill="y")
        self.lista.pack(side="left", fill="both", expand=True)
        self.lista.bind("<Double-Button-1>", self.duplo_clique)
        self.lista.bind("<Delete>", lambda e: self.remover_selecionada())
        self.lista.bind("<Button-3>", self.clique_direito)

        botoes = tk.Frame(painel, bg=PAINEL)
        botoes.pack(fill="x", padx=6, pady=(4, 6))
        for texto, cmd, cor in (("✎ editar", self.editar_selecionada, AZUL),
                                ("✓ feita/reabrir", self.alternar_selecionada, VERDE),
                                ("✕ remover", self.remover_selecionada, VERMELHO),
                                ("⌫ limpar feitas", self.limpar_feitas, AMARELO),
                                ("➕ adicionar", self.abrir_janela_adicionar, VERDE)):
            self.botao(botoes, texto, cmd, cor, PAINEL, padx=8, pady=2).pack(side="left", padx=(0, 6))

        # Terminal
        term = tk.Frame(paned, bg=BG)

        linha = tk.Frame(term, bg=BG)
        linha.pack(side="bottom", fill="x")
        self.prompt = tk.Label(linha, text="", bg=BG, fg=VERDE,
                               font=(self.fonte, self.tam, "bold"))
        self.prompt.pack(side="left", padx=(8, 4), pady=4)
        self.entrada = tk.Entry(linha, bg=BG, fg=BRANCO, insertbackground=BRANCO,
                                relief="flat", highlightthickness=0, borderwidth=0,
                                font=(self.fonte, self.tam))
        self.entrada.pack(side="left", fill="x", expand=True, pady=4)
        alca = tk.Label(linha, text="◢", bg=BG, fg=MUDO, cursor="size_nw_se")
        alca.pack(side="right", padx=4)
        alca.bind("<ButtonPress-1>", self.inicio_redim)
        alca.bind("<B1-Motion>", self.redimensionar)

        self.saida = tk.Text(term, bg=BG, fg=FG, relief="flat", borderwidth=0,
                             highlightthickness=0, wrap="char", padx=8, pady=6,
                             font=(self.fonte, self.tam), state="disabled",
                             insertwidth=0, spacing1=1)
        sb = tk.Scrollbar(term, command=self.saida.yview)
        self.saida.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.saida.pack(side="left", fill="both", expand=True)

        for tag, cor in (("out", FG), ("cmd", BRANCO), ("prompt", VERDE), ("path", AZUL),
                         ("dim", MUDO), ("err", VERMELHO), ("ok", VERDE), ("dir", AZUL)):
            self.saida.tag_config(tag, foreground=cor)
        self.saida.tag_config("prompt", font=(self.fonte, self.tam, "bold"))
        self.saida.tag_config("dir", font=(self.fonte, self.tam, "bold"))

        # Clicar na área de saída devolve o foco ao prompt (a menos que haja seleção)
        self.saida.bind("<ButtonRelease-1>", self.foco_prompt)

        paned.add(painel, minsize=90, height=170)
        paned.add(term, minsize=140, stretch="always")

        e = self.entrada
        e.bind("<Return>", self.enviar)
        e.bind("<Up>", self.hist_anterior)
        e.bind("<Down>", self.hist_proximo)
        e.bind("<Tab>", self.completar)
        e.bind("<Control-c>", self.ctrl_c)
        e.bind("<Control-l>", lambda ev: (self.limpar_tela(), "break")[1])

    def foco_prompt(self, _):
        if not self.saida.tag_ranges("sel"):
            self.entrada.focus_set()

    # ---------------------------------------------------------------- saída
    def escrever(self, texto, tag="out"):
        s = self.saida
        s.config(state="normal")
        s.insert("end", texto, tag)
        if int(s.index("end-1c").split(".")[0]) > 3000:   # limita o histórico na tela
            s.delete("1.0", "500.0")
        s.config(state="disabled")
        s.see("end")

    def limpar_tela(self):
        self.saida.config(state="normal")
        self.saida.delete("1.0", "end")
        self.saida.config(state="disabled")

    def eco(self, cmd):
        self.escrever(f"{self.usuario}@{self.host}", "prompt")
        self.escrever(":", "dim")
        self.escrever(self.caminho_curto(), "path")
        self.escrever("$ ", "dim")
        self.escrever(cmd + "\n", "cmd")

    def caminho_curto(self):
        p, h = str(self.cwd), str(Path.home())
        if p == h:
            return "~"
        if p.startswith(h + os.sep):
            return "~" + p[len(h):]
        return p

    def atualizar_prompt(self):
        if self.ocupado:
            self.prompt.config(text="⏳", fg=AMARELO)
        else:
            self.prompt.config(text=f"{self.usuario}@{self.host}:{self.caminho_curto()}$",
                               fg=VERDE)

    def banner(self):
        self.escrever("Terminal de Tarefas", "prompt")
        self.escrever("  —  digite ", "dim")
        self.escrever("help", "cmd")
        self.escrever(" para ver os comandos. Feche só pelo ✕ do menu.\n\n", "dim")

    # -------------------------------------------------------------- comandos
    def enviar(self, _=None):
        cmd = self.entrada.get().strip()
        self.entrada.delete(0, "end")
        if self.ocupado:
            self.escrever("processo em execução — use Ctrl+C para interromper\n", "err")
            return
        self.eco(cmd)
        if not cmd:
            return
        if not self.historico or self.historico[-1] != cmd:
            self.historico.append(cmd)
        self.pos_hist = len(self.historico)
        self.interpretar(cmd)

    def interpretar(self, cmd):
        partes = cmd.split(None, 1)
        nome = partes[0].lower()
        resto = partes[1] if len(partes) > 1 else ""

        if re.match(r"^cd(\s|$|\.\.|\\|/)", cmd, re.I):
            self.cmd_cd(cmd[2:])
        elif WIN and re.fullmatch(r"[A-Za-z]:", cmd):
            self.cmd_cd(cmd)
        elif nome in ("todo", "tarefa"):
            self.cmd_todo(resto)
        elif nome in ("clear", "cls"):
            self.limpar_tela()
        elif nome == "pwd":
            self.escrever(str(self.cwd) + "\n")
        elif nome in ("exit", "quit"):
            self.escrever("use o botão ✕ do menu (canto superior direito) para fechar.\n", "dim")
        elif nome in ("help", "ajuda", "?"):
            self.cmd_help()
        elif WIN and nome == "ls":
            self.cmd_ls(resto)
        else:
            self.executar_shell(cmd)

    def cmd_help(self):
        linhas = [
            ("cd <pasta>", "entra na pasta (cd .. | cd ~ | cd - | cd sozinho = home | D: troca de disco)"),
            ("ls / dir", "lista a pasta atual"),
            ("pwd", "mostra a pasta atual"),
            ("clear / cls", "limpa a tela (ou Ctrl+L)"),
            ("todo", "lista as tarefas"),
            ("todo add <texto>", "cria uma tarefa"),
            ("todo edit <n> <texto>", "troca o texto (sem <texto>: abre no prompt para editar)"),
            ("todo done <n...>", "marca como feita (aceita 1 3 5 ou 2-4)"),
            ("todo undone <n...>", "reabre a(s) tarefa(s)"),
            ("todo rm <n...>", "remove tarefa(s)"),
            ("todo clear", "remove todas as concluídas"),
            ("Tab / ↑ ↓", "completa caminhos / histórico"),
            ("Ctrl+C", "interrompe o comando em execução"),
            ("<qualquer outro>", "roda no shell da máquina, na pasta atual"),
        ]
        for a, b in linhas:
            self.escrever(f"  {a:<24}", "path")
            self.escrever(b + "\n", "dim")

    # ---- cd / ls
    def resolver(self, arg, nome="cd"):
        arg = arg.strip()
        if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
            arg = arg[1:-1]
        if WIN and re.fullmatch(r"[A-Za-z]:", arg):
            arg += os.sep
        destino = Path(os.path.expandvars(os.path.expanduser(arg)))
        if not destino.is_absolute():
            destino = self.cwd / destino
        try:
            destino = destino.resolve(strict=True)
        except (OSError, RuntimeError):
            self.escrever(f"{nome}: {arg}: caminho não encontrado\n", "err")
            return None
        if not destino.is_dir():
            self.escrever(f"{nome}: {arg}: não é uma pasta\n", "err")
            return None
        try:
            os.listdir(destino)
        except OSError:
            self.escrever(f"{nome}: {arg}: sem permissão\n", "err")
            return None
        return destino

    def cmd_cd(self, arg):
        arg = arg.strip()
        if WIN:
            arg = re.sub(r"^/d\s+", "", arg, flags=re.I)
        if arg == "":
            destino = Path.home()
        elif arg == "-":
            destino = self.anterior
        else:
            destino = self.resolver(arg, "cd")
            if destino is None:
                return
        self.anterior, self.cwd = self.cwd, destino
        self.atualizar_prompt()
        if arg == "-":
            self.escrever(str(self.cwd) + "\n", "dim")

    def cmd_ls(self, args):
        ocultos, partes = False, []
        for a in args.split():
            if a.startswith("-"):
                ocultos = ocultos or "a" in a
            else:
                partes.append(a)
        alvo = self.resolver(" ".join(partes), "ls") if partes else self.cwd
        if alvo is None:
            return
        pastas, arquivos = [], []
        try:
            for nome in os.listdir(alvo):
                caminho = alvo / nome
                oculto = nome.startswith(".")
                try:
                    oculto = oculto or bool(getattr(os.stat(caminho), "st_file_attributes", 0) & 2)
                except OSError:
                    pass
                if oculto and not ocultos:
                    continue
                (pastas if caminho.is_dir() else arquivos).append(nome)
        except OSError as e:
            self.escrever(f"ls: {e}\n", "err")
            return
        itens = [(n + os.sep, "dir") for n in sorted(pastas, key=str.lower)]
        itens += [(n, "out") for n in sorted(arquivos, key=str.lower)]
        if not itens:
            return
        f = tkfont.Font(family=self.fonte, size=self.tam)
        largura_chars = max(20, (self.saida.winfo_width() - 20) // max(1, f.measure("0")))
        col = max(len(n) for n, _ in itens) + 2
        n_cols = max(1, largura_chars // col)
        for i, (nome, tag) in enumerate(itens):
            fim = "\n" if (i + 1) % n_cols == 0 or i == len(itens) - 1 else ""
            self.escrever(nome.ljust(col) if not fim else nome, tag)
            if fim:
                self.escrever("\n")

    # ---- tarefas pelo terminal
    def cmd_todo(self, resto):
        sub, _, arg = resto.strip().partition(" ")
        sub, arg = sub.lower(), arg.strip()
        if sub in ("", "ls", "list"):
            self.imprimir_tarefas()
        elif sub in ("add", "new", "+"):
            if not arg:
                self.escrever("uso: todo add <texto>\n", "err")
                return
            self.tarefas.append({"texto": arg, "feita": False})
            self.persistir()
            self.escrever(f"+ tarefa {len(self.tarefas)} criada\n", "ok")
        elif sub in ("edit", "ed"):
            self.cmd_editar(arg)
        elif sub in ("done", "feita"):
            self.definir_feitas(arg, True)
        elif sub in ("undone", "reabrir"):
            self.definir_feitas(arg, False)
        elif sub in ("rm", "del", "remove"):
            self.cmd_remover(arg)
        elif sub in ("clear", "limpar"):
            self.limpar_feitas()
        else:
            self.escrever("uso: todo [ls | add <texto> | edit <n> <texto> | done <n...> | "
                          "undone <n...> | rm <n...> | clear]\n", "err")

    def persistir(self):
        self.salvar()
        self.atualizar_tarefas()

    def cmd_editar(self, arg):
        num, _, novo = arg.partition(" ")
        i = self.indice(num)
        if i is None:
            return
        novo = novo.strip()
        if not novo:                       # sem texto novo: abre a tarefa no prompt para editar
            self.preencher_edicao(i)
            self.escrever(f"editando a tarefa {i + 1}: ajuste o texto no prompt e aperte Enter\n", "dim")
            return
        antigo = self.tarefas[i]["texto"]
        self.tarefas[i]["texto"] = novo    # editar NÃO muda se está feita ou não
        self.persistir()
        self.escrever(f"~ tarefa {i + 1}: {antigo}  ->  {novo}\n", "ok")

    def preencher_edicao(self, i):
        self.entrada.delete(0, "end")
        self.entrada.insert(0, f"todo edit {i + 1} {self.tarefas[i]['texto']}")
        self.entrada.focus_set()
        self.entrada.icursor("end")

    def definir_feitas(self, arg, valor):
        idx = self.indices(arg)
        if idx is None:
            return
        for i in idx:
            self.tarefas[i]["feita"] = valor
        self.persistir()
        nums = ", ".join(str(i + 1) for i in idx)
        self.escrever(f"~ {nums}: {'marcada(s) como feita(s)' if valor else 'reaberta(s)'}\n", "ok")

    def cmd_remover(self, arg):
        idx = self.indices(arg)
        if idx is None:
            return
        for i in reversed(idx):            # de trás para frente para não bagunçar os números
            t = self.tarefas.pop(i)
            self.escrever(f"- removida {i + 1}: {t['texto']}\n", "ok")
        self.persistir()

    def indices(self, arg):
        """Converte '1 3 5', '1,3' ou '2-4' em índices (0-based) válidos; None se inválido."""
        achados = set()
        for tok in re.split(r"[,\s]+", arg.strip()):
            if not tok:
                continue
            m = re.fullmatch(r"(\d+)(?:-(\d+))?", tok)
            if not m:
                return self._invalido()
            a = int(m.group(1))
            b = int(m.group(2) or a)
            for n in range(a, b + 1):
                if not 1 <= n <= len(self.tarefas):
                    return self._invalido()
                achados.add(n - 1)
        if not achados:
            return self._invalido()
        return sorted(achados)

    def _invalido(self):
        self.escrever("número de tarefa inválido (veja com: todo)\n", "err")
        return None

    def indice(self, arg):
        idx = self.indices(arg)
        if idx is None:
            return None
        if len(idx) != 1:
            self.escrever("informe só um número de tarefa\n", "err")
            return None
        return idx[0]

    def imprimir_tarefas(self):
        if not self.tarefas:
            self.escrever("nenhuma tarefa.\n", "dim")
            return
        for i, t in enumerate(self.tarefas):
            marca = "[x]" if t["feita"] else "[ ]"
            self.escrever(f" {i + 1:>2}  {marca}  {t['texto']}\n", "dim" if t["feita"] else "out")

    # ---- shell real
    def executar_shell(self, cmd):
        self.ocupado = True
        self.atualizar_prompt()
        threading.Thread(target=self._worker, args=(cmd,), daemon=True).start()

    def _worker(self, cmd):
        try:
            kw = dict(shell=True, cwd=str(self.cwd), stdin=subprocess.DEVNULL,
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if WIN:
                kw["creationflags"] = SEM_JANELA
            else:
                kw["start_new_session"] = True
            self.proc = subprocess.Popen(cmd, **kw)
            for linha in iter(self.proc.stdout.readline, b""):
                self.fila.put(("out", linha.decode(self.enc, errors="replace")))
            self.proc.wait()
            self.fila.put(("fim", self.proc.returncode))
        except Exception as e:
            self.fila.put(("err", f"{e}\n"))
            self.fila.put(("fim", 1))

    def drenar_fila(self):
        try:
            while True:
                tipo, dado = self.fila.get_nowait()
                if tipo == "out":
                    limpo = ANSI.sub("", dado).replace("\r\n", "\n").replace("\r", "\n")
                    self.escrever(limpo, "out")
                elif tipo == "err":
                    self.escrever(dado, "err")
                elif tipo == "fim":
                    self.ocupado = False
                    self.proc = None
                    if self.saida.get("end-2c", "end-1c") != "\n":
                        self.escrever("\n")
                    if dado not in (0, None):
                        self.escrever(f"[saiu com código {dado}]\n", "dim")
                    self.atualizar_prompt()
        except queue.Empty:
            pass
        self.root.after(40, self.drenar_fila)

    def interromper(self):
        p = self.proc
        if not p or p.poll() is not None:
            return
        try:
            if WIN:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               creationflags=SEM_JANELA, capture_output=True)
            else:
                os.killpg(p.pid, signal.SIGTERM)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        self.escrever("^C\n", "dim")

    def ctrl_c(self, _):
        if self.ocupado:
            self.interromper()
            return "break"
        if not self.entrada.selection_present():   # sem seleção: descarta a linha (como no terminal)
            self.entrada.delete(0, "end")
            return "break"
        return None                                 # com seleção: copia normalmente

    # ---- histórico e Tab
    def hist_anterior(self, _):
        if self.historico and self.pos_hist > 0:
            self.pos_hist -= 1
            self.entrada.delete(0, "end")
            self.entrada.insert(0, self.historico[self.pos_hist])
        return "break"

    def hist_proximo(self, _):
        self.entrada.delete(0, "end")
        if self.pos_hist < len(self.historico) - 1:
            self.pos_hist += 1
            self.entrada.insert(0, self.historico[self.pos_hist])
        else:
            self.pos_hist = len(self.historico)
        return "break"

    def completar(self, _):
        pos = self.entrada.index("insert")
        antes = self.entrada.get()[:pos]
        parte = re.search(r"\S*$", antes).group(0)
        base, prefixo = os.path.split(parte)
        pasta = Path(os.path.expandvars(os.path.expanduser(base))) if base else Path(".")
        if not pasta.is_absolute():
            pasta = self.cwd / pasta
        try:
            nomes = [n for n in os.listdir(pasta) if n.lower().startswith(prefixo.lower())]
        except OSError:
            return "break"
        if not nomes:
            return "break"
        nomes.sort(key=str.lower)
        comum = os.path.commonprefix([n.lower() for n in nomes])
        novo = nomes[0][:len(comum)] if len(nomes) > 1 else nomes[0]
        if len(nomes) == 1 and (pasta / nomes[0]).is_dir():
            novo += os.sep
        if len(nomes) > 1 and len(novo) <= len(prefixo):
            self.escrever("  ".join(nomes) + "\n", "dim")
            return "break"
        inicio = pos - len(parte)
        self.entrada.delete(inicio, pos)
        self.entrada.insert(inicio, parte[:len(parte) - len(prefixo)] + novo)
        return "break"

    # ------------------------------------------------------- tarefas (painel)
    def atualizar_tarefas(self):
        sel = self.lista.curselection()
        sel = sel[0] if sel else None
        self.lista.delete(0, "end")
        for i, t in enumerate(self.tarefas):
            marca = "[x]" if t["feita"] else "[ ]"
            self.lista.insert("end", f" {i + 1:>2}  {marca}  {t['texto']}")
            self.lista.itemconfig(i, fg=MUDO if t["feita"] else FG)
        if sel is not None and self.tarefas:
            self.lista.selection_set(min(sel, len(self.tarefas) - 1))
        feitas = sum(t["feita"] for t in self.tarefas)
        self.info.config(text=f"{feitas}/{len(self.tarefas)} concluídas")

    def abrir_janela_adicionar(self):
        janela = tk.Toplevel(self.root)
        janela.title("Adicionar Tarefa")
        janela.geometry("300x200")
        janela.configure(bg=PAINEL)

        tk.Label(janela, text="Texto:", bg=PAINEL, fg=FG).pack(pady=5)
        entry_texto = tk.Entry(janela)
        entry_texto.pack()

        tk.Label(janela, text="Prioridade:", bg=PAINEL, fg=FG).pack(pady=5)
        combo_prioridade = ttk.Combobox(janela, values=["Baixa", "Média", "Alta"])
        combo_prioridade.current(1)
        combo_prioridade.pack()

        def salvar_nova():
            texto = entry_texto.get()
            if texto:
                self.tarefas.append({
                    "texto": texto,
                    "feita": False,
                    "prioridade": combo_prioridade.get(),
                    "data": ""
                })
                self.persistir()
                janela.destroy()

        tk.Button(janela, text="Salvar", command=salvar_nova).pack(pady=10)

    def selecionada(self):
        s = self.lista.curselection()
        return s[0] if s else None

    def editar_selecionada(self):
        i = self.selecionada()
        if i is not None:
            self.preencher_edicao(i)

    def alternar_selecionada(self):
        i = self.selecionada()
        if i is None:
            return
        self.tarefas[i]["feita"] = not self.tarefas[i]["feita"]
        self.salvar()
        self.atualizar_tarefas()

    def remover_selecionada(self):
        i = self.selecionada()
        if i is None:
            return
        del self.tarefas[i]
        self.salvar()
        self.atualizar_tarefas()

    def limpar_feitas(self):
        antes = len(self.tarefas)
        self.tarefas = [t for t in self.tarefas if not t["feita"]]
        self.salvar()
        self.atualizar_tarefas()
        if len(self.tarefas) != antes:
            self.escrever(f"- {antes - len(self.tarefas)} tarefa(s) concluída(s) removida(s)\n", "ok")

    def indice_do_clique(self, ev):
        i = self.lista.nearest(ev.y)
        return i if 0 <= i < len(self.tarefas) else None

    def duplo_clique(self, ev):
        i = self.indice_do_clique(ev)
        if i is None:
            return
        self.tarefas[i]["feita"] = not self.tarefas[i]["feita"]
        self.salvar()
        self.atualizar_tarefas()

    def clique_direito(self, ev):
        i = self.indice_do_clique(ev)
        if i is None:
            return
        del self.tarefas[i]
        self.salvar()
        self.atualizar_tarefas()

    # ------------------------------------------------------------------- menu
    def alternar_topo(self):
        self.topo = not self.topo
        self.root.attributes("-topmost", self.topo)
        self.b_topo.config(fg=AZUL if self.topo else MUDO)

    def trocar_opacidade(self):
        self.nivel = (self.nivel + 1) % len(self.niveis)
        self.root.attributes("-alpha", self.niveis[self.nivel])

    def fechar(self):
        self.interromper()
        self.salvar()
        self.root.destroy()

    # ------------------------------------------------- mover / redimensionar
    def inicio_arrasto(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def arrastar(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def inicio_redim(self, e):
        self._rx, self._ry = e.x_root, e.y_root
        self._w, self._h = self.root.winfo_width(), self.root.winfo_height()

    def redimensionar(self, e):
        w = max(460, self._w + e.x_root - self._rx)
        h = max(360, self._h + e.y_root - self._ry)
        self.root.geometry(f"{w}x{h}")

    def rodar(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().rodar()
