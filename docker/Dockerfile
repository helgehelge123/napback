FROM alpine:3.23
RUN apk add --no-cache rsync acl
# No server, network listener, credentials or Docker socket. The PC starts an
# isolated sender over existing SSH and Docker removes it when rsync exits.
CMD ["rsync", "--version"]
