#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>
#include <arpa/inet.h>

extern char **environ;
int main() {
  if (getuid() == 0 || geteuid() == 0) return 1;
  long max_fd = sysconf(_SC_OPEN_MAX);
  if (max_fd < 0) return 2;
  for (int i = 3; i < max_fd; ++i) {
    errno = 0;
    if (fcntl(i, F_GETFD) != -1 || errno != EBADF) return 2;
  }
  for (char **e = environ; *e; ++e) {
    std::string v(*e);
    if (v.compare(0, 5, "PATH=") && v.compare(0, 5, "HOME=") &&
        v.compare(0, 7, "TMPDIR=")) return 3;
  }
  struct stat st;
  if (stat("/var/run/docker.sock", &st) == 0 ||
      stat("/run/containerd/containerd.sock", &st) == 0 ||
      stat("/input/.git", &st) == 0) return 4;
  if (access("/root/.git-credentials", R_OK) == 0 ||
      access("/root/.ssh", R_OK) == 0) return 5;
  std::ifstream status("/proc/self/status");
  std::string line;
  bool cap = false, nnp = false;
  while (std::getline(status, line)) {
    if (line == "CapEff:\t0000000000000000") cap = true;
    if (line == "NoNewPrivs:\t1") nnp = true;
  }
  if (!cap || !nnp) return 6;
  int s = socket(AF_INET, SOCK_STREAM, 0);
  if (s < 0) return 7;
  sockaddr_in addr{};
  addr.sin_family = AF_INET; addr.sin_port = htons(443);
  inet_pton(AF_INET, "1.1.1.1", &addr.sin_addr);
  int rc = connect(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));
  int e = errno;
  close(s);
  if (rc == 0 || e != ENETUNREACH) return 8;
  std::puts("CANARY_PASS: nonroot, no inherited fds, clean env, no host sockets, no effective caps, no-new-privs, network unreachable");
  return 0;
}
