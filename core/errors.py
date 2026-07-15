"""Erros de validação/parsing do domínio, sem dependência de framework web."""


class CalcError(Exception):
    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)
