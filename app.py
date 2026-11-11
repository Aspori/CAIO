import os
import re
import sqlite3
from functools import wraps

from flask import Flask, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, "public")
DB = os.path.join(BASE, "estoque.db")

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave")


# ---------- banco ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def fechar(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS usuarios(
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
            papel TEXT NOT NULL DEFAULT 'admin',
            dono_id INTEGER,
            ativo INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS produtos(
            id INTEGER PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            codigo TEXT NOT NULL,
            nome TEXT NOT NULL,
            categoria TEXT NOT NULL,
            custo REAL NOT NULL DEFAULT 0,
            venda REAL NOT NULL DEFAULT 0,
            qtd INTEGER NOT NULL DEFAULT 0,
            minimo INTEGER NOT NULL DEFAULT 0,
            UNIQUE(usuario_id, codigo));
        CREATE TABLE IF NOT EXISTS movimentacoes(
            id INTEGER PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            produto_id INTEGER,
            produto_nome TEXT NOT NULL,
            tipo TEXT NOT NULL,
            quantidade INTEGER NOT NULL,
            entregue_por TEXT,
            recebido_por TEXT,
            registrado_por TEXT,
            contato TEXT,
            preco_venda REAL,
            preco_custo REAL,
            criado_em TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
        """
    )
    ucols = [r[1] for r in conn.execute("PRAGMA table_info(usuarios)")]
    for c, tipo in (("papel", "TEXT NOT NULL DEFAULT 'admin'"), ("dono_id", "INTEGER"), ("ativo", "INTEGER NOT NULL DEFAULT 1")):
        if c not in ucols:
            conn.execute(f"ALTER TABLE usuarios ADD COLUMN {c} {tipo}")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(movimentacoes)")]
    for c in ("entregue_por", "recebido_por", "registrado_por", "contato"):
        if c not in cols:
            conn.execute(f"ALTER TABLE movimentacoes ADD COLUMN {c} TEXT")
    if "preco_venda" not in cols:
        conn.execute("ALTER TABLE movimentacoes ADD COLUMN preco_venda REAL")
        conn.execute("ALTER TABLE movimentacoes ADD COLUMN preco_custo REAL")
        conn.execute("UPDATE movimentacoes SET preco_venda=(SELECT venda FROM produtos p WHERE p.id=movimentacoes.produto_id), preco_custo=(SELECT custo FROM produtos p WHERE p.id=movimentacoes.produto_id)")
    if not conn.execute("SELECT 1 FROM usuarios").fetchone():
        conn.execute(
            "INSERT INTO usuarios(email, senha_hash) VALUES(?,?)",
            ("admin@loja.com", generate_password_hash("123456")),
        )
    conn.execute("UPDATE usuarios SET dono_id=id WHERE dono_id IS NULL")
    conn.commit()
    conn.close()


def atual():
    """Usuário logado (lido do banco a cada pedido, só se estiver ativo)."""
    if "uid" not in session:
        return None
    if "u" not in g:
        g.u = db().execute(
            "SELECT id, email, papel, dono_id FROM usuarios WHERE id=? AND ativo=1", (session["uid"],)).fetchone()
    return g.u


def loja():
    """Id da loja (conta do admin) a que o usuário logado pertence."""
    return atual()["dono_id"]


def logado(f):
    @wraps(f)
    def wrap(*a, **k):
        if not atual():
            return jsonify(erro="Faça login para continuar."), 401
        return f(*a, **k)
    return wrap


def admin(f):
    @wraps(f)
    @logado
    def wrap(*a, **k):
        if atual()["papel"] != "admin":
            return jsonify(erro="Só o administrador pode fazer isso."), 403
        return f(*a, **k)
    return wrap


# ---------- páginas ----------
@app.get("/")
@app.get("/index.html")
def home():
    return send_from_directory(PUBLIC, "index.html")


@app.get("/login.html")
def pagina_login():
    return send_from_directory(PUBLIC, "login.html")


@app.get("/recursos.html")
def pagina_recursos():
    return send_from_directory(PUBLIC, "recursos.html")


@app.get("/movimentacoes.html")
def pagina_movimentacoes():
    if not atual():
        return redirect("/login.html")
    return send_from_directory(PUBLIC, "movimentacoes.html")


@app.get("/relatorios.html")
def pagina_relatorios():
    if not atual():
        return redirect("/login.html")
    return send_from_directory(PUBLIC, "relatorios.html")


@app.get("/equipe.html")
def pagina_equipe():
    u = atual()
    if not u:
        return redirect("/login.html")
    if u["papel"] != "admin":
        return redirect("/app.html")
    return send_from_directory(PUBLIC, "equipe.html")


@app.get("/cadastro.html")
def pagina_cadastro():
    return send_from_directory(PUBLIC, "cadastro.html")


@app.get("/app.html")
def pagina_app():
    if not atual():
        return redirect("/login.html")
    return send_from_directory(PUBLIC, "app.html")


@app.get("/style.css")
def css():
    return send_from_directory(PUBLIC, "style.css")


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login.html")


# ---------- api: login ----------
@app.post("/api/login")
def api_login():
    d = request.get_json(silent=True) or {}
    u = db().execute("SELECT * FROM usuarios WHERE email=?", (d.get("email", "").strip().lower(),)).fetchone()
    if not u or not check_password_hash(u["senha_hash"], d.get("senha", "")):
        return jsonify(erro="E-mail ou senha incorretos."), 401
    if not u["ativo"]:
        return jsonify(erro="Esta conta está desativada. Fale com o administrador."), 403
    session["uid"] = u["id"]
    return jsonify(ok=True)


@app.post("/api/registrar")
def api_registrar():
    d = request.get_json(silent=True) or {}
    email = str(d.get("email", "")).strip().lower()
    senha = str(d.get("senha", ""))
    if "@" not in email or "." not in email.split("@")[-1]:
        return jsonify(erro="Digite um e-mail válido."), 400
    if len(senha) < 6:
        return jsonify(erro="A senha precisa ter pelo menos 6 caracteres."), 400
    try:
        cur = db().execute("INSERT INTO usuarios(email, senha_hash, papel) VALUES(?,?,'admin')", (email, generate_password_hash(senha)))
        db().execute("UPDATE usuarios SET dono_id=id WHERE id=?", (cur.lastrowid,))
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(erro="Esse e-mail já tem conta. Tente entrar."), 409
    session["uid"] = cur.lastrowid
    return jsonify(ok=True), 201


# ---------- api: produtos ----------
def ler_produto(d):
    try:
        p = dict(
            codigo=str(d.get("codigo", "")).strip(),
            nome=str(d.get("nome", "")).strip(),
            categoria=str(d.get("categoria", "")).strip(),
            custo=float(d.get("custo") or 0),
            venda=float(d.get("venda") or 0),
            qtd=int(d.get("qtd") or 0),
            minimo=int(d.get("minimo") or 0),
        )
    except (TypeError, ValueError):
        return None, "Confira os números: preço e quantidade precisam ser válidos."
    if not p["nome"] or not p["codigo"]:
        return None, "Nome e código são obrigatórios."
    if min(p["custo"], p["venda"], p["qtd"], p["minimo"]) < 0:
        return None, "Valores não podem ser negativos."
    return p, None


@app.get("/api/produtos")
@logado
def listar():
    rows = db().execute("SELECT * FROM produtos WHERE usuario_id=? ORDER BY id DESC", (loja(),)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/produtos")
@admin
def criar():
    p, erro = ler_produto(request.get_json(silent=True) or {})
    if erro:
        return jsonify(erro=erro), 400
    p["uid"] = loja()
    try:
        cur = db().execute(
            "INSERT INTO produtos(usuario_id,codigo,nome,categoria,custo,venda,qtd,minimo) VALUES(:uid,:codigo,:nome,:categoria,:custo,:venda,:qtd,:minimo)", p)
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(erro="Já existe um produto com esse código."), 409
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/produtos/<int:pid>")
@admin
def atualizar(pid):
    p, erro = ler_produto(request.get_json(silent=True) or {})
    if erro:
        return jsonify(erro=erro), 400
    try:
        cur = db().execute(
            "UPDATE produtos SET codigo=:codigo,nome=:nome,categoria=:categoria,custo=:custo,venda=:venda,minimo=:minimo WHERE id=:id AND usuario_id=:uid",
            {**p, "id": pid, "uid": loja()})
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(erro="Já existe um produto com esse código."), 409
    if cur.rowcount == 0:
        return jsonify(erro="Produto não encontrado."), 404
    return jsonify(ok=True)


@app.delete("/api/produtos/<int:pid>")
@admin
def excluir(pid):
    db().execute("DELETE FROM produtos WHERE id=? AND usuario_id=?", (pid, loja()))
    db().commit()
    return jsonify(ok=True)


def registrar_venda(itens, d):
    """Vende vários itens de uma vez, tudo ou nada. itens = [(produto_id, quantidade)]."""
    entregue = str(d.get("entregue_por", "")).strip()[:80]
    recebido = str(d.get("recebido_por", "")).strip()[:80]
    contato = str(d.get("contato", "")).strip()[:120]
    if not entregue or not recebido:
        return jsonify(erro="Informe quem entregou e quem recebeu."), 400
    if not itens:
        return jsonify(erro="Adicione pelo menos um produto."), 400
    uid, cx = loja(), db()
    for pid, qtd in itens:
        p = cx.execute("SELECT nome, venda, custo, qtd FROM produtos WHERE id=? AND usuario_id=?", (pid, uid)).fetchone()
        if not p:
            cx.rollback()
            return jsonify(erro="Produto não encontrado."), 404
        cur = cx.execute(
            "UPDATE produtos SET qtd=qtd-? WHERE id=? AND usuario_id=? AND qtd>=?", (qtd, pid, uid, qtd))
        if cur.rowcount == 0:
            cx.rollback()
            return jsonify(erro=f"Estoque insuficiente de {p['nome']} (tem {p['qtd']})."), 409
        cx.execute(
            "INSERT INTO movimentacoes(usuario_id,produto_id,produto_nome,tipo,quantidade,entregue_por,recebido_por,contato,"
            "preco_venda,preco_custo,registrado_por) VALUES(?,?,?,'venda',?,?,?,?,?,?,?)",
            (uid, pid, p["nome"], qtd, entregue, recebido, contato, p["venda"], p["custo"], atual()["email"]))
    cx.commit()
    return jsonify(ok=True)


@app.post("/api/produtos/<int:pid>/venda")
@logado
def vender(pid):
    return registrar_venda([(pid, 1)], request.get_json(silent=True) or {})


@app.post("/api/vendas")
@logado
def vendas():
    d = request.get_json(silent=True) or {}
    soma = {}
    try:
        for it in d.get("itens", []):
            pid, qtd = int(it["produto_id"]), int(it["quantidade"])
            if not 1 <= qtd <= 100000:
                raise ValueError
            soma[pid] = soma.get(pid, 0) + qtd
    except (KeyError, TypeError, ValueError):
        return jsonify(erro="Confira os produtos e as quantidades."), 400
    if len(soma) > 100:
        return jsonify(erro="No máximo 100 produtos por venda."), 400
    return registrar_venda(list(soma.items()), d)


@app.post("/api/produtos/<int:pid>/entrada")
@admin
def entrada(pid):
    d = request.get_json(silent=True) or {}
    try:
        qtd = int(d.get("quantidade"))
    except (TypeError, ValueError):
        qtd = 0
    if not 1 <= qtd <= 100000:
        return jsonify(erro="Informe uma quantidade de 1 a 100000."), 400
    entregue = str(d.get("entregue_por", "")).strip()[:80]
    recebido = str(d.get("recebido_por", "")).strip()[:80]
    if not entregue or not recebido:
        return jsonify(erro="Informe quem entregou e quem recebeu."), 400
    uid, cx = loja(), db()
    p = cx.execute("SELECT nome, venda, custo FROM produtos WHERE id=? AND usuario_id=?", (pid, uid)).fetchone()
    if not p:
        return jsonify(erro="Produto não encontrado."), 404
    cx.execute("UPDATE produtos SET qtd=qtd+? WHERE id=? AND usuario_id=?", (qtd, pid, uid))
    cx.execute(
        "INSERT INTO movimentacoes(usuario_id,produto_id,produto_nome,tipo,quantidade,entregue_por,recebido_por,preco_venda,preco_custo,registrado_por) VALUES(?,?,?,'entrada',?,?,?,?,?,?)",
        (uid, pid, p["nome"], qtd, entregue, recebido, p["venda"], p["custo"], atual()["email"]))
    cx.commit()
    return jsonify(ok=True)


# ---------- api: movimentações ----------
@app.get("/api/movimentacoes")
@logado
def movimentacoes():
    rows = db().execute(
        "SELECT * FROM movimentacoes WHERE usuario_id=? ORDER BY id DESC LIMIT 200", (loja(),)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/me")
@logado
def me():
    u = atual()
    return jsonify(email=u["email"], papel=u["papel"])


# ---------- api: equipe (só admin) ----------
@app.get("/api/equipe")
@admin
def equipe_listar():
    rows = db().execute(
        "SELECT id, email, ativo FROM usuarios WHERE dono_id=? AND papel='operador' ORDER BY id", (loja(),)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/equipe")
@admin
def equipe_criar():
    d = request.get_json(silent=True) or {}
    email = str(d.get("usuario", "")).strip().lower()  # o nome de usuário fica na coluna email
    senha = str(d.get("senha", ""))
    if not re.fullmatch(r"[a-z0-9._-]{3,30}", email):
        return jsonify(erro="Nome de usuário: de 3 a 30 caracteres, só letras, números, ponto, traço ou sublinhado, sem espaços."), 400
    if len(senha) < 6:
        return jsonify(erro="A senha precisa ter pelo menos 6 caracteres."), 400
    try:
        db().execute(
            "INSERT INTO usuarios(email, senha_hash, papel, dono_id) VALUES(?,?,'operador',?)",
            (email, generate_password_hash(senha), loja()))
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(erro="Esse nome de usuário já está em uso."), 409
    return jsonify(ok=True), 201


@app.put("/api/equipe/<int:oid>")
@admin
def equipe_editar(oid):
    d = request.get_json(silent=True) or {}
    op = db().execute(
        "SELECT id FROM usuarios WHERE id=? AND dono_id=? AND papel='operador'", (oid, loja())).fetchone()
    if not op:
        return jsonify(erro="Operador não encontrado."), 404
    if "senha" in d:
        if len(str(d["senha"])) < 6:
            return jsonify(erro="A senha precisa ter pelo menos 6 caracteres."), 400
        db().execute("UPDATE usuarios SET senha_hash=? WHERE id=?", (generate_password_hash(str(d["senha"])), oid))
    if "ativo" in d:
        db().execute("UPDATE usuarios SET ativo=? WHERE id=?", (1 if d["ativo"] else 0, oid))
    db().commit()
    return jsonify(ok=True)


@app.get("/api/relatorio")
@logado
def relatorio():
    rows = db().execute(
        "SELECT tipo, quantidade, preco_venda, preco_custo, criado_em FROM movimentacoes "
        "WHERE usuario_id=? AND criado_em >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-91 days') "
        "ORDER BY criado_em LIMIT 20000", (loja(),)).fetchall()
    return jsonify([dict(r) for r in rows])


init_db()

if __name__ == "__main__":
    app.run(debug=True)
