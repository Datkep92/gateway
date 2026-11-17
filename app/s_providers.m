#import "gw.h"

@interface ProviderDetailVC ()
@property (nonatomic, strong) NSDictionary *prov;
@end

#pragma mark - danh sách nhà cung cấp

@implementation ProvidersVC

- (void)viewDidLoad {
    self.title = @"Nhà cung cấp";
    [super viewDidLoad];
    self.navigationItem.rightBarButtonItem =
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemAdd
                                                      target:self action:@selector(addKey)];
}

- (void)addKey {
    [self push:[[KeyAddVC alloc] init]];
}

- (void)loadData {
    [self.sections removeAllObjects];
    GWSection *s = [self addSection:nil footer:nil];
    GWRow *wait = [self addRow:s title:@"Đang đọc…" sub:nil];
    wait.dotKind = 4;
    [self.tableView reloadData];

    [[GWA shared] get:@"/gateway/providers" query:nil done:^(id j, NSError *e) {
        [self.sections removeAllObjects];
        if (e != nil) {
            GWSection *bad = [self addSection:nil footer:nil];
            [self addRow:bad title:@"Không đọc được" sub:e.localizedDescription].dotKind = 2;
            [self addAddRow];
            [self.tableView reloadData];
            return;
        }
        NSArray *ps = j[@"providers"];
        GWSection *sec = [self addSection:nil
                                   footer:@"Chạm vào một nhà cung cấp để xem và sửa địa chỉ, key, model của nó."];
        for (NSDictionary *p in ps) {
            NSString *name = p[@"name"] ?: @"";
            NSString *label = p[@"label"] ?: gwProviderLabel(name);
            NSInteger keys = [p[@"key_count"] integerValue];
            NSInteger models = [p[@"model_count"] integerValue];
            GWRow *r = [self addRow:sec
                              title:label
                                sub:[NSString stringWithFormat:@"%@ · %ld key · %ld model",
                                     p[@"base_url"] ?: @"(chưa có địa chỉ)", (long)keys, (long)models]];
            r.value = keys ? @"đang dùng" : @"chưa có key";
            r.chevron = YES;
            r.dotKind = keys ? 1 : 3;
            if (keys == 0) r.dotKind = 4;
            r.extra = name;
            r.tap = ^(GWRow *row) {
                ProviderDetailVC *d = [[ProviderDetailVC alloc] init];
                d.name = name;
                [self push:d];
            };
        }
        if (ps.count == 0) {
            [self addRow:sec title:@"Chưa có nhà cung cấp nào" sub:@"Bấm ＋ để thêm key của một nhà cung cấp."].dotKind = 4;
        }
        [self addAddRow];
        [self.tableView reloadData];
    }];
}

- (void)addAddRow {
    GWSection *s = [self addSection:nil footer:@"Thêm key xong, cổng tự dò model và tự kiểm."];
    GWRow *r = [self addRow:s title:@"＋ Thêm key cho nhà cung cấp" sub:nil];
    r.tap = ^(GWRow *row) { [self addKey]; };
}

@end

#pragma mark - chi tiết một nhà cung cấp

@implementation ProviderDetailVC

- (void)viewDidLoad {
    self.title = gwProviderLabel(self.name);
    [super viewDidLoad];
}

- (void)loadData {
    if (self.name.length == 0) return;
    [self.sections removeAllObjects];
    GWSection *s = [self addSection:nil footer:nil];
    GWRow *w = [self addRow:s title:@"Đang đọc…" sub:nil];
    w.dotKind = 4;
    [self.tableView reloadData];

    GWA *api = [GWA shared];
    [api get:@"/gateway/providers" query:nil done:^(id j, NSError *e) {
        NSDictionary *prov = nil;
        for (NSDictionary *p in j[@"providers"]) {
            if ([p[@"name"] isEqualToString:self.name]) { prov = p; break; }
        }
        if (prov == nil) {
            [self.sections removeAllObjects];
            GWSection *bad = [self addSection:nil footer:nil];
            [self addRow:bad title:@"Không thấy nhà cung cấp này"
                      sub:e ? e.localizedDescription : @"Có thể nó đã bị xoá."].dotKind = 2;
            [self.tableView reloadData];
            return;
        }
        self.prov = prov;
        [api get:@"/gateway/keys" query:@{@"provider": self.name} done:^(id j2, NSError *e2) {
            [api get:@"/gateway/models" query:nil done:^(id j3, NSError *e3) {
                [self buildWithKeys:j2[@"keys"] models:j3[@"models"]];
            }];
        }];
    }];
}

- (void)buildWithKeys:(NSArray *)keys models:(NSArray *)allModels {
    [self.sections removeAllObjects];
    NSDictionary *p = self.prov;

    GWSection *info = [self addSection:@"Địa chỉ & đường dẫn"
                                footer:@"Chạm vào một dòng để sửa. Địa chỉ đúng thì cổng mới gọi được."];
    [self fieldRow:info field:@"base_url"   title:@"Địa chỉ (base URL)"   value:p[@"base_url"]];
    [self fieldRow:info field:@"protocol"   title:@"Giao thức"            value:p[@"protocol"]];
    [self fieldRow:info field:@"chat_path"  title:@"Đường dẫn chat"       value:p[@"chat_path"]];
    [self fieldRow:info field:@"messages_path" title:@"Đường dẫn messages" value:p[@"messages_path"]];
    [self fieldRow:info field:@"models_path"   title:@"Đường dẫn danh sách model" value:p[@"models_path"]];
    [self fieldRow:info field:@"key_url"    title:@"Trang lấy key"        value:p[@"key_url"]];

    GWSection *act = [self addSection:nil footer:nil];
    GWRow *addk = [self addRow:act title:@"＋ Thêm key cho nhà cung cấp này" sub:nil];
    addk.tap = ^(GWRow *r) {
        KeyAddVC *a = [[KeyAddVC alloc] init];
        a.provider = self.name;
        [self push:a];
    };
    GWRow *disc = [self addRow:act title:@"Dò lại danh sách model" sub:@"Hỏi máy chủ của nhà cung cấp xem có model nào"];
    disc.tap = ^(GWRow *r) { [self discover]; };
    GWRow *del = [self addRow:act title:@"Xoá nhà cung cấp" sub:@"Xoá cả key và model của nó"];
    del.tap = ^(GWRow *r) { [self confirmDelete]; };

    NSArray *mine = [self keysOf:keys];
    GWSection *ks = [self addSection:[NSString stringWithFormat:@"Key (%lu)", (unsigned long)mine.count] footer:nil];
    if (mine.count == 0) {
        [self addRow:ks title:@"Chưa có key" sub:@"Thêm key thì model của nhà cung cấp này mới dùng được."].dotKind = 4;
    }
    for (NSDictionary *k in mine) {
        GWRow *r = gwKeyRow(k);
        [ks.rows addObject:r];
        gwKeyTap(self, k, ^{ [self loadData]; });
    }

    NSMutableArray *models = [NSMutableArray array];
    for (NSDictionary *m in allModels) {
        if ([m[@"provider"] isEqualToString:self.name]) [models addObject:m];
    }
    GWSection *ms = [self addSection:[NSString stringWithFormat:@"Model (%lu)", (unsigned long)models.count]
                              footer:@"Chạm vào một model để kiểm tra, bật/tắt hoặc xoá."];
    if (models.count == 0) {
        [self addRow:ms title:@"Chưa có model" sub:@"Bấm “Dò lại danh sách model”."].dotKind = 4;
    }
    for (NSDictionary *m in models) {
        GWRow *r = gwModelRow(m);
        [ms.rows addObject:r];
        gwModelSwitch(self, r, m, ^{ [self loadData]; });
        gwModelTap(self, m, ^{ [self loadData]; });
    }
    [self.tableView reloadData];
}

- (NSArray *)keysOf:(NSArray *)keys {
    NSMutableArray *out = [NSMutableArray array];
    for (NSDictionary *k in keys) {
        if ([k[@"provider"] isEqualToString:self.name]) [out addObject:k];
    }
    return out;
}

- (void)fieldRow:(GWSection *)s field:(NSString *)field title:(NSString *)title value:(NSString *)value {
    GWRow *r = [self addRow:s title:title sub:(value.length ? value : @"(trống)")];
    r.tap = ^(GWRow *row) {
        [self prompt:title value:value run:^(NSString *text) {
            [self saveField:field value:text];
        }];
    };
}

- (void)saveField:(NSString *)field value:(NSString *)value {
    [self busy:@"Đang lưu…"];
    NSDictionary *body = @{field: value ?: @""};
    NSString *path = [NSString stringWithFormat:@"/gateway/providers/%@", self.name];
    [[GWA shared] send:@"PATCH" path:path body:body done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không lưu được"]; return; }
        [self toast:@"Đã lưu"];
        [self loadData];
    }];
}

- (void)discover {
    [self busy:@"Đang hỏi máy chủ của nhà cung cấp…"];
    [[GWA shared] send:@"POST" path:@"/gateway/providers/discover" body:@{@"provider": self.name}
                  done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không dò được"]; return; }
        NSInteger found = [j[@"found"] integerValue];
        [self toast:[NSString stringWithFormat:@"Tìm thấy %ld model", (long)found]];
        [self loadData];
    }];
}

- (void)confirmDelete {
    NSString *name = self.name;
    UIAlertController *ac = [UIAlertController
        alertControllerWithTitle:[NSString stringWithFormat:@"Xoá %@?", gwProviderLabel(name)]
                         message:@"Toàn bộ key và model của nhà cung cấp này sẽ bị xoá."
                  preferredStyle:UIAlertControllerStyleAlert];
    [ac addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
    [ac addAction:[UIAlertAction actionWithTitle:@"Xoá" style:UIAlertActionStyleDestructive
                                        handler:^(UIAlertAction *a) {
        [self busy:@"Đang xoá…"];
        [[GWA shared] send:@"DELETE" path:[NSString stringWithFormat:@"/gateway/providers/%@", name]
                      body:nil done:^(id j, NSError *e) {
            [self done];
            if (e != nil) { [self report:e title:@"Không xoá được"]; return; }
            [self.navigationController popViewControllerAnimated:YES];
        }];
    }]];
    [self presentViewController:ac animated:YES completion:nil];
}

@end
