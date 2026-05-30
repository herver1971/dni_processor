# Acceso por Tailscale

DNI Processor se expone al tailnet usando **Tailscale Serve** — el mismo mecanismo con el que Escriba está disponible en `https://hernan-disco.tail778471.ts.net`.

## Contexto

El servicio bindea a `127.0.0.1:8001` (HTTP puro). No es accesible desde la red local ni desde internet directamente. Tailscale actúa como reverse proxy: termina TLS con certificado autogestionado y ruteea el tráfico HTTPS entrante del tailnet hacia el loopback del servidor.

## Configurar el acceso

Una vez que el systemd está corriendo (ver [Instalación](instalacion.md)):

```bash
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8001
```

Desglose del comando:

| Flag / argumento | Significado |
|---|---|
| `--bg` | Persiste la configuración (sobrevive a la sesión SSH, se restaura con `tailscaled`) |
| `--https=8443` | Expone HTTPS en el puerto 8443 del hostname Tailscale del servidor |
| `http://127.0.0.1:8001` | Destino: el servicio local en HTTP |

La URL de acceso queda:

```
https://hernan-disco.tail778471.ts.net:8443/
```

## Verificar

```bash
tailscale serve status
```

Debe mostrar algo como:

```
https://hernan-disco.tail778471.ts.net:8443 (tailnet only)
|-- / proxy http://127.0.0.1:8001
```

Desde otra máquina del tailnet:

```bash
curl https://hernan-disco.tail778471.ts.net:8443/api/v1/health
```

## Distinción con Escriba

Escriba está en `https://hernan-disco.tail778471.ts.net/operaciones` (puerto 443, path `/operaciones`). DNI Processor está en el puerto 8443. Son configuraciones de Tailscale Serve independientes en el mismo hostname.

!!! info "¿Por qué puerto diferente en lugar del mismo `/dni-processor`?"
    Técnicamente se puede hacer `tailscale serve --set-path=/dni-processor http://127.0.0.1:8001`, pero la app tiene paths hardcoded (`/static/`, `/api/v1/`). Cuando se accede bajo un subpath, el browser pide `/static/main.css` en lugar de `/dni-processor/static/main.css` y la UI se rompe.
    
    Resolver esto requiere configurar `root_path` en FastAPI y refactorear los templates para usar `url_for()`. Es trabajo para un sprint dedicado de UI. Por ahora, el puerto alternativo es la solución más simple.

## Acceso desde Escriba

Cuando se implemente la integración en Fase 5, Escriba puede llamar a DNI Processor directamente en `http://127.0.0.1:8001` (ambos en el mismo servidor) o por `https://hernan-disco.tail778471.ts.net:8443` si eventualmente quedan en servidores distintos.

Para la integración en el mismo servidor, el acceso directo a loopback es más eficiente — no pasa por Tailscale.

## Troubleshooting

### `curl` falla con `Connection refused`

1. Verificar que el servicio está corriendo: `systemctl status dni_processor`
2. Verificar que Tailscale está up: `tailscale status`
3. Verificar que la configuración de Serve existe: `tailscale serve status`

### El browser muestra error de certificado

Los certificados de Tailscale requieren que HTTPS esté habilitado en el tailnet. Verificar en el admin panel de Tailscale (Settings → DNS → HTTPS certificates).

### `tailscale serve status` muestra la config pero el browser no carga

```bash
# En el servidor, el servicio tiene que escuchar en el puerto correcto:
ss -tlnp | grep 8001
# Debe mostrar 127.0.0.1:8001

# Si no aparece, el servicio está caído o en otro puerto:
sudo systemctl restart dni_processor
```

### Otro device del tailnet no alcanza la URL

- ¿El device tiene Tailscale activo? `tailscale status` desde ese device.
- ¿Están en el mismo tailnet? El hostname `hernan-disco.tail778471.ts.net` es solo alcanzable desde el tailnet `tail778471.ts.net`.
