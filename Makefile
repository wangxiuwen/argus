APP     = Argus
OUT     = .build/$(APP)
BUNDLE  = $(HOME)/Applications/$(APP).app
BIN_DIR = $(HOME)/.local/bin

.PHONY: build install uninstall clean

build:
	mkdir -p .build
	swiftc -O -o $(OUT) Argus/main.swift

install: build
	mkdir -p $(BUNDLE)/Contents/MacOS $(BIN_DIR)
	cp $(OUT) $(BUNDLE)/Contents/MacOS/
	cp Info.plist $(BUNDLE)/Contents/
	codesign --force --sign - $(BUNDLE)
	install -m 755 bin/argus $(BIN_DIR)/argus
	@echo "installed: $(BUNDLE) and $(BIN_DIR)/argus"
	@echo "open the tray app with: open $(BUNDLE)"

uninstall:
	-$(BIN_DIR)/argus stop
	rm -rf $(BUNDLE)
	rm -f $(BIN_DIR)/argus

clean:
	rm -rf .build
