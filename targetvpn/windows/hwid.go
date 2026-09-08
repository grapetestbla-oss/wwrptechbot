package main

import (
	"crypto/sha256"
	"encoding/hex"
	"os/exec"
	"runtime"
	"strings"
)

// hwid считает идентификатор машины из MachineGuid реестра Windows.
// Значение стабильно до переустановки системы; на сервер уходит только хеш.
func hwid() string {
	raw := machineGUID() + "|" + runtime.GOOS + "|" + hostName()
	sum := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(sum[:])
}

func machineGUID() string {
	out, err := exec.Command("reg", "query",
		`HKLM\SOFTWARE\Microsoft\Cryptography`, "/v", "MachineGuid").Output()
	if err != nil {
		return "unknown-machine"
	}
	// Формат строки: "    MachineGuid    REG_SZ    <значение>"
	for _, line := range strings.Split(string(out), "\n") {
		if strings.Contains(line, "MachineGuid") {
			fields := strings.Fields(line)
			if len(fields) >= 3 {
				return fields[len(fields)-1]
			}
		}
	}
	return "unknown-machine"
}

func hostName() string {
	out, err := exec.Command("hostname").Output()
	if err != nil {
		return "pc"
	}
	return strings.TrimSpace(string(out))
}
