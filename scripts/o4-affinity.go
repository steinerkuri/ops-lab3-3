//go:build linux

// 先绑定与 systemd 方案相同的单个 CPU，再 exec enrolld；不另起常驻进程。
package main

import (
	"log"
	"os"
	"runtime"
	"strconv"
	"syscall"
	"unsafe"
)

func main() {
	runtime.LockOSThread()
	cpu, err := strconv.Atoi(os.Getenv("LAB3_INSTANCE_CPU"))
	if err != nil || cpu < 0 || cpu >= 1024 {
		log.Fatal("LAB3_INSTANCE_CPU 必须是 0～1023 的单个 CPU 编号")
	}
	var mask [128]byte
	mask[cpu/8] |= byte(1 << (cpu % 8))
	_, _, errno := syscall.RawSyscall(syscall.SYS_SCHED_SETAFFINITY, 0,
		uintptr(len(mask)), uintptr(unsafe.Pointer(&mask[0])))
	if errno != 0 {
		log.Fatalf("CPU 绑核失败，不能继续做可比实验：%v", errno)
	}
	if err := syscall.Exec("/enrolld", append([]string{"/enrolld"}, os.Args[1:]...), os.Environ()); err != nil {
		log.Fatal(err)
	}
}
