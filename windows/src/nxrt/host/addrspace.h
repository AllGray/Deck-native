// Guest memory primitives over the placeholders and the guest section (host/addrspace.c)
#pragma once
#include <windows.h>
#include <stdint.h>

int as_have_placeholders(void);
int as_commit_private(uint64_t a, uint64_t n, DWORD prot);   // a free placeholder range → private committed memory
int as_release_private(uint64_t a, uint64_t n);              // private memory → placeholder
int as_section_offset(uint64_t a, uint64_t n, uint64_t *off);   // where a range of the alias / stack / heap regions lies in the section
int as_section_read(uint64_t off, void *dst, uint64_t n);    // Linux: pools placed in Vulkan memory (vk_mem.c)
int as_section_write(uint64_t off, const void *src, uint64_t n);
int as_section_punch(uint64_t off, uint64_t n);
int as_section_map(uint64_t a, uint64_t n, uint64_t off);
int as_map_view(uint64_t a, uint64_t n, uint64_t off);       // a view of the section at a (a placeholder range)
int as_unmap_view(uint64_t a);                               // the view at a → placeholder
void as_coalesce(uint64_t lo, uint64_t hi);
int as_guest_views(void);                                    // alias and heap views (once)
int as_commit(uint64_t a, uint64_t n);
int as_protect(uint64_t a, uint64_t n, DWORD prot);
int as_discard(uint64_t a, uint64_t n);
