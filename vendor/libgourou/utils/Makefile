BINDIR ?= /bin
MANDIR ?= /share/man

TARGET_BINARIES=acsmdownloader adept_activate adept_remove adept_loan_mgt
TARGETS=$(TARGET_BINARIES) launcher

MAN_PAGES=acsmdownloader adept_activate adept_remove adept_loan_mgt

CXXFLAGS=-Wall -fPIC -I$(ROOT)/include -fmacro-prefix-map=$(ROOT)/= -fdata-sections -ffunction-sections

STATIC_DEP=
# LDFLAGS += -Wl,--gc-sections
LDFLAGS += -L$(ROOT) -lcrypto -lzip -lz -lcurl -lpugixml

ifneq ($(STATIC_UTILS),)
STATIC_DEP = $(ROOT)/libgourou.a
else
LDFLAGS += -lgourou
endif

ifneq ($(DEBUG),)
CXXFLAGS += -ggdb -O0 -DDEBUG
else
CXXFLAGS += -O2
endif


COMMON_DEPS = drmprocessorclientimpl.cpp utils_common.cpp
COMMON_OBJECTS = $(COMMON_DEPS:.cpp=.o)
COMMON_LIB  = utils.a

all: $(TARGETS)

${COMMON_LIB}: $(COMMON_DEPS)
	$(CXX) $(CXXFLAGS) $(COMMON_DEPS) -c
	$(AR) crs $@ $(COMMON_OBJECTS)

%: %.cpp $(COMMON_LIB) $(STATIC_DEP)
	$(CXX) $(CXXFLAGS) $^ $(STATIC_DEP) $(LDFLAGS) -o $@

install: $(TARGET_BINARIES)
	install -d $(DESTDIR)$(PREFIX)/$(BINDIR)
	install -m 755 $(TARGET_BINARIES) $(DESTDIR)$(PREFIX)/$(BINDIR)
	install -d $(DESTDIR)$(PREFIX)/$(MANDIR)/man1
	install -m 644 man/*.1 $(DESTDIR)$(PREFIX)/$(MANDIR)/man1

uninstall:
	cd $(DESTDIR)$(PREFIX)/$(BINDIR)
	rm -f $(TARGET_BINARIES)
	cd -
	cd $(DESTDIR)$(PREFIX)/$(MANDIR)/man1
	rm -f $(addsuffix .1,$(TARGET_BINARIES)

clean:
	rm -f $(TARGETS) $(COMMON_LIB)

ultraclean: clean
