# TokioAI — Despliegue con Mesh de Tailscale

Conecta TokioAI a cualquier hardware, en cualquier lugar, desde cualquier red. Costo adicional: cero.

## Resumen

TokioAI usa [Tailscale](https://tailscale.com) para crear una VPN mesh segura entre la nube (donde corre TokioAI) y tu hardware local (Raspberry Pi, routers, dispositivos IoT, servidores). Esto significa:

- TokioAI en la nube puede controlar hardware en tu casa
- Podés cambiar de red y todo se reconecta automáticamente
- Sin puertos expuestos a internet, sin port forwarding, sin DNS dinámico
- El plan gratuito cubre hasta 100 dispositivos

## Arquitectura

```
                       Mesh de Tailscale (WireGuard)
                     ================================

  VM en la Nube (GCP/AWS/VPS)         Hardware Local
  ┌──────────────────────┐           ┌──────────────────┐
  │  TokioAI Agent       │◄─────────►│  Raspberry Pi    │
  │  (28+ herramientas,  │  Tailscale│  - GPIO/relays   │
  │   Claude/GPT/Gemini) │  100.x.x  │  - sensores      │
  │                      │           │  - cámaras       │
  │  Bot de Telegram     │           └──────────────────┘
  │  (ACL multi-usuario) │
  │                      │           ┌──────────────────┐
  │  WAF/SOC             │◄─────────►│  Servidor Local  │
  │  (opcional, comparte │  Tailscale│  - nodo backup   │
  │   postgres + kafka)  │  100.x.x  │  - subnet router │
  └──────────────────────┘           │    → acceso LAN  │
           ▲                         └──────────────────┘
           │                                  │
           │ Tailscale                        │ Ruta de Subred
           │ 100.x.x.x                       │ 192.168.x.0/24
           ▼                                  ▼
  ┌──────────────────┐              ┌──────────────────┐
  │  Tu Celular/     │              │  Router           │
  │  Laptop          │              │  (control SSH)    │
  │  (app Tailscale) │              │  Cualquier equipo │
  └──────────────────┘              │  de la LAN        │
                                    └──────────────────┘
```

## Guía de Configuración

### 1. Instalar Tailscale en todas las máquinas

```bash
# Funciona en Linux, macOS, Raspberry Pi, etc.
curl -fsSL https://tailscale.com/install.sh | sh
```

### 2. Autenticar la VM en la nube

```bash
sudo tailscale up
# Abre un link en el navegador para autenticar — iniciá sesión con tu cuenta
```

### 3. Autenticar las máquinas locales

En cada máquina local (Raspberry Pi, servidor):

```bash
sudo tailscale up
```

### 4. Habilitar enrutamiento de subred (opcional pero recomendado)

Si querés que TokioAI acceda a dispositivos en tu red local (routers, impresoras, NAS, etc.), elegí una máquina en tu LAN como **subnet router**:

```bash
# En la máquina que será subnet router (ej: tu servidor local):
sudo sysctl -w net.ipv4.ip_forward=1
echo 'net.ipv4.ip_forward=1' | sudo tee -a /etc/sysctl.conf
sudo tailscale up --advertise-routes=192.168.8.0/24
```

Después aprobá la ruta en la [consola de administración de Tailscale](https://login.tailscale.com/admin/machines) y aceptá las rutas en la VM de la nube:

```bash
# En la VM de la nube:
sudo tailscale set --accept-routes=true
```

### 5. Desplegar TokioAI en la nube

```bash
# Copiar el proyecto a tu VM
scp -r tokioai-v2/ usuario@tu-vm:/opt/tokioai-v2/

# Editar .env con tus API keys y token de Telegram
cp .env.example .env
nano .env

# Si compartís un PostgreSQL existente (recomendado):
docker compose -f docker-compose.cloud.yml up -d

# Si corrés standalone:
docker compose up -d
```

### 6. Montar SSH keys para control remoto

Si TokioAI necesita hacer SSH a routers o hosts a través de la mesh:

```bash
# Crear directorio para las SSH keys en la VM
mkdir -p /opt/tokioai-v2/ssh-keys/

# Copiar tus SSH keys
cp id_ed25519_router /opt/tokioai-v2/ssh-keys/
chmod 600 /opt/tokioai-v2/ssh-keys/*

# Descomentar los volume mounts en docker-compose.cloud.yml
# Después reiniciar:
docker compose -f docker-compose.cloud.yml up -d
```

## Cómo funciona

### Red de containers

Los containers Docker en una red bridge pueden alcanzar destinos de Tailscale a través de la tabla de ruteo del host. No necesita configuración especial — si el host puede llegar a `192.168.8.1` vía Tailscale, los containers también.

### Aislamiento de sesiones

Cada usuario de Telegram tiene su propio session ID (`telegram-{user_id}`) guardado en PostgreSQL. Las conversaciones nunca se mezclan entre usuarios. Agregar usuarios:

```bash
# En .env:
TELEGRAM_ALLOWED_IDS=id_usuario1,id_usuario2

# O por Telegram (solo el owner):
/allow 123456789
```

### Reconexión

Tailscale maneja toda la reconexión automáticamente:
- Cambios de red (WiFi → datos móviles → otro WiFi)
- Cortes del ISP (reconecta cuando vuelve internet)
- Reinicios de la máquina (el servicio systemd arranca automáticamente)

## Verificar conectividad

Desde tu VM en la nube, testeá que podés llegar a todo:

```bash
# Peers directos de Tailscale
sudo tailscale status

# Dispositivos por ruta de subred (ej: tu router)
ping 192.168.8.1

# Desde dentro del container de TokioAI
docker exec tokio-agent python3 -c "
import socket
for ip, port, name in [('192.168.8.1', 22, 'Router'), ('100.x.x.x', 22, 'Raspi')]:
    try:
        s=socket.socket(); s.settimeout(3); s.connect((ip, port))
        print(f'{name}: OK'); s.close()
    except: print(f'{name}: FALLO')
"
```

## Costo

- **Tailscale**: Gratis (hasta 100 dispositivos)
- **Overhead en la VM**: ~15 MB RAM para el daemon tailscaled
- **Overhead de containers**: ~88 MB RAM (tokio-agent + bot de telegram)
- **Ancho de banda**: Despreciable (comandos de texto, no streams de video)
- **Sin cargos adicionales de GCP/AWS** por tráfico de Tailscale

## Seguridad

- Todo el tráfico encriptado con WireGuard (el protocolo subyacente de Tailscale)
- Sin puertos expuestos públicamente para TokioAI — solo webhook de Telegram
- SSH keys montadas como solo lectura en los containers
- Credenciales API en `.env` (en gitignore, nunca se commitean)
- Las rutas de subred requieren aprobación explícita en la consola de Tailscale
- Cada máquina se autentica individualmente vía SSO de Tailscale

## Agregar nuevo hardware

Para agregar un nuevo dispositivo (otra Raspberry Pi, gateway Arduino, hub de sensores):

```bash
# En el nuevo dispositivo:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Autenticá con el link del navegador
# El dispositivo aparece inmediatamente en tu mesh — TokioAI ya puede alcanzarlo
```

Sin reglas de firewall, sin configs de VPN, sin cambios de DNS. Solo `tailscale up`.
