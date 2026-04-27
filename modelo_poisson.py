import math

def poisson_prob(lmbda, k):
    return (lmbda ** k * math.exp(-lmbda)) / math.factorial(k)

def matriz_resultados(lambda_home, lambda_away, max_gols=5):

    matriz = []

    for i in range(max_gols+1):
        linha = []
        for j in range(max_gols+1):
            p = poisson_prob(lambda_home, i) * poisson_prob(lambda_away, j)
            linha.append(p)
        matriz.append(linha)

    return matriz

def prob_vitoria(matriz):

    home = draw = away = 0

    for i in range(len(matriz)):
        for j in range(len(matriz)):
            if i > j:
                home += matriz[i][j]
            elif i == j:
                draw += matriz[i][j]
            else:
                away += matriz[i][j]

    return home, draw, away